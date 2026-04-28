import streamlit as st
import math
import requests
from datetime import datetime, timedelta
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from streamlit_folium import st_folium
import pandas as pd

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run VRPTW Manager", page_icon="🚚", layout="wide")
st.title("🚚 ระบบจัดเส้นทางนมพร้อมกรอบเวลา (VRPTW Optimization)")
st.markdown("ระบุเวลาที่ลูกค้าสะดวกรับของ และเวลาเริ่มออกรถ เพื่อให้ระบบจัดสรรคิวที่ตรงเวลาที่สุด")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน (Operational)")
    # เพิ่มส่วนตั้งเวลาเริ่มส่ง
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("10:30", "%H:%M").time())
    SERVICE_TIME = st.slider("เวลาลงนมเฉลี่ยต่อจุด (นาที)", 1, 15, 3)
    
    st.header("⛽ ต้นทุนและพื้นที่")
    THB_L = st.slider("ราคาน้ำมัน (THB/L)", 20.0, 50.0, 40.0, 0.5)
    KM_L = st.slider("อัตราสิ้นเปลือง (km/L)", 5.0, 20.0, 12.0, 0.5)
    TOTAL_CAP = st.number_input("ความจุสุทธิ (ลิตร)", value=1570)

# แปลงเวลาออกรถเป็นนาทีนับจากเที่ยงคืนเพื่อใช้คำนวณ
start_minutes = DEPART_TIME.hour * 60 + DEPART_TIME.minute

# ==========================================
# 3. ส่วนจัดการข้อมูล (Dynamic Data Editor)
# ==========================================
st.subheader("📍 จัดการพิกัด ยอดสินค้า และกรอบเวลา (Time Windows)")

default_data = [
    {"ชื่อสถานที่": "โรงฟาร์ม มทส.", "Lat": 14.8890708, "Lon": 102.0006967, "ยอด (L)": 0, "เริ่มรับได้": "08:00", "ต้องส่งก่อน": "18:00"}
]

edited_df = st.data_editor(default_data, num_rows="dynamic", use_container_width=True)

# ==========================================
# 4. ฟังก์ชันคำนวณ (Core Engine)
# ==========================================
def time_to_min(t_str):
    try:
        h, m = map(int, t_str.split(':'))
        return h * 60 + m
    except:
        return 0

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

# ==========================================
# 5. ประมวลผลเส้นทาง
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณเส้นทางและเวลา (Run Optimization)", type="primary", use_container_width=True):
    if edited_df['Lat'].isna().any() or len(edited_df) < 2:
        st.warning("กรุณากรอกพิกัดให้ครบถ้วน")
        st.stop()
        
    with st.spinner('สมองกลกำลังจัดคิวตามกรอบเวลา...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        # ประมาณการเวลาเดินทางเบื้องต้น (ความเร็วเฉลี่ย 30 กม./ชม.)
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        time_matrix = [[int((d / 1000) / 30 * 60) + SERVICE_TIME for d in row] for row in dist_matrix]
        
        # จัดเตรียมข้อมูล Time Windows
        time_windows = []
        for _, row in edited_df.iterrows():
            time_windows.append((time_to_min(row["เริ่มรับได้"]), time_to_min(row["ต้องส่งก่อน"])))

        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)

        # ตั้งค่ามิติเรื่องเวลา (Time Dimension)
        def time_callback(from_index, to_index):
            return time_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
        
        time_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.AddDimension(time_callback_index, 30, 1440, False, "Time")
        time_dimension = routing.GetDimensionOrDie("Time")

        # ใส่เงื่อนไข Time Window ของแต่ละจุด
        for i, window in enumerate(time_windows):
            index = manager.NodeToIndex(i)
            time_dimension.CumulVar(index).SetRange(window[0], window[1])

        # ตั้งค่ามิติน้ำหนัก
        def demand_callback(from_index):
            return int(edited_df.iloc[manager.IndexToNode(from_index)]["ยอด (L)"])
        demand_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_index, 0, [TOTAL_CAP], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        solution = routing.SolveWithParameters(search_params)

    if not solution:
        st.error("❌ หาเส้นทางที่ตรงตามเวลาไม่ได้! ลองขยายกรอบเวลาของลูกค้าหรือขยับเวลาออกรถ")
    else:
        # ดึงลำดับ
        route_indices = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            route_indices.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route_indices.append(manager.IndexToNode(index))

        # เรียก TomTom จริงเพื่อเอาเวลาเดินทางที่แม่นยำ
        route_coords = [f"{coords[n][0]},{coords[n][1]}" for n in route_indices]
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join(route_coords)}/json"
        res = requests.get(url, params={"key": API_KEY, "routeType": "fastest", "departAt": "now"})
        
        if res.status_code == 200:
            data = res.json()
            legs = data['routes'][0]['legs']
            
            st.success("✅ จัดคิวสำเร็จตามกรอบเวลา!")
            
            col_map, col_table = st.columns([1.5, 1])
            with col_map:
                m = folium.Map(location=coords[0], zoom_start=13)
                # (ส่วนวาดแผนที่และไอคอนตัวเลขเหมือนเดิม)
                st_folium(m, width="100%", height=500)

            with col_table:
                st.subheader("📊 ตารางเวลาและระยะเวลาเดินทาง")
                schedule = []
                curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                
                for i, n in enumerate(route_indices):
                    travel_min = 0
                    if i > 0 and i-1 < len(legs):
                        travel_min = round(legs[i-1]['summary']['travelTimeInSeconds'] / 60, 1)
                        curr_time += timedelta(minutes=travel_min)
                    
                    schedule.append({
                        "คิว": i,
                        "สถานที่": edited_df.iloc[n]["ชื่อสถานที่"],
                        "เดินทางจากจุดก่อนหน้า (นาที)": travel_min if i > 0 else "-",
                        "ถึงเวลา (ETA)": curr_time.strftime("%H:%M")
                    })
                    curr_time += timedelta(minutes=SERVICE_TIME) # บวกเวลาลงนม
                
                st.table(pd.DataFrame(schedule).set_index("คิว"))
