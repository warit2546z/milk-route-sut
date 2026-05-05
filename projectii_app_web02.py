import streamlit as st
import math
import requests
from datetime import datetime, timedelta
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from streamlit_folium import st_folium
import pandas as pd
import io

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run Optimization", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางขนส่งนม (VRP Optimization)")
st.markdown("เน้นการคำนวณเส้นทางที่ประหยัดที่สุด และการออกรายงานในรูปแบบ Excel")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ต้นทุนและพื้นที่บรรทุก")
    THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=40.0, step=0.5, format="%.2f")
    KM_L = st.number_input("อัตราสิ้นเปลือง (km/L)", min_value=1.0, value=12.0, step=0.5, format="%.2f")
    NUM_COOLERS = st.number_input("จำนวนถัง (ใบ)", min_value=1, value=2, step=1)
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=15.0, step=1.0)
    DEAD_SPACE_RATIO = 0.15 # พื้นที่เผื่อช่องว่าง 15%

TOTAL_NET_CAPACITY = int((800 - ICE_PER_COOLER) * NUM_COOLERS)
EMISSION_FACTOR = 2.70757206 

# ==========================================
# 3. การจัดการข้อมูล (Data Management)
# ==========================================
st.subheader("📍 นำเข้าข้อมูลจุดจัดส่ง")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel หรือ CSV)", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        edited_df = st.data_editor(df, num_rows="dynamic", height=300, use_container_width=True)
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์ Excel ที่มีคอลัมน์: ชื่อสถานที่, Lat, Lon, 200cc, 2L, 5L, เริ่มรับได้, ต้องส่งก่อน")
    st.stop()

# ==========================================
# 4. ส่วนการคำนวณ (Optimization Core)
# ==========================================
def time_to_min(t_str):
    try:
        h, m = map(int, str(t_str).split(':'))
        return h * 60 + m
    except: return None 

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

st.markdown("---")
if st.button("🚀 ประมวลผลเส้นทางและวิเคราะห์ความคุ้มค่า", type="primary", use_container_width=True):
    # คำนวณความต้องการ (Demand)
    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (float(row.get("200cc", 0)) * 0.2) + (float(row.get("2L", 0)) * 2.0) + (float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    if sum(demands) > TOTAL_NET_CAPACITY:
        st.error(f"❌ น้ำหนักรวม ({sum(demands)} L) เกินความจุของรถ ({TOTAL_NET_CAPACITY} L)")
        st.stop()
        
    with st.spinner('AI กำลังค้นหาเส้นทางที่ประหยัดที่สุด...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        # เปรียบเทียบเส้นทางดั้งเดิม (As-Is)
        baseline_km = sum([dist_matrix[i][i+1] for i in range(len(coords)-1)] + [dist_matrix[len(coords)-1][0]]) / 1000
        
        # สร้างโมเดลการจัดเส้นทาง (OR-Tools)
        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_cb_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)
        
        # เพิ่มเงื่อนไขด้านเวลา (Soft Time Windows)
        routing.AddDimension(transit_cb_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.CumulVar(routing.Start(0)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            time_dim.CumulVar(idx).SetRange(s, 2880)
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100) # Penalty 100 per minute

        # เพิ่มเงื่อนไขด้านความจุ (Capacity)
        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_cb_idx, 0, [TOTAL_NET_CAPACITY], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
        solution = routing.SolveWithParameters(search_params)

    if solution:
        # ดึงลำดับเส้นทาง
        route_indices = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            route_indices.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route_indices.append(0)

        # คำนวณสรุปผลผ่าน TomTom API
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join([f'{coords[n][0]},{coords[n][1]}' for n in route_indices])}/json"
        res = requests.get(url, params={"key": API_KEY})
        
        if res.status_code == 200:
            route_data = res.json()['routes'][0]
            dist_km = route_data['summary']['lengthInMeters'] / 1000
            cost = (dist_km / KM_L) * THB_L
            
            # --- แสดง Dashboard ---
            st.subheader("📊 ผลการวิเคราะห์ (Optimization Analysis)")
            c1, c2, c3 = st.columns(3)
            c1.metric("ระยะทางรวม (To-Be)", f"{dist_km:.2f} กม.", f"{dist_km - baseline_km:.2f} กม.")
            c2.metric("ต้นทุนน้ำมัน", f"฿{cost:.2f}", f"฿{((dist_km - baseline_km)/KM_L)*THB_L:.2f}")
            c3.metric("คาร์บอนฟุตพริ้นท์", f"{(dist_km/KM_L)*EMISSION_FACTOR:.2f} kg", f"{((dist_km - baseline_km)/KM_L)*EMISSION_FACTOR:.2f} kg")

            # --- ตารางแผนงาน ---
            schedule = []
            curr_time = datetime.combine(datetime.today(), DEPART_TIME)
            for i, n in enumerate(route_indices[:-1]):
                if i > 0:
                    leg_time = math.ceil(route_data['legs'][i-1]['summary']['travelTimeInSeconds']/60)
                    curr_time += timedelta(minutes=leg_time)
                
                loc = edited_df.iloc[n]
                schedule.append({
                    "ลำดับการส่ง": i,
                    "สถานที่": loc["ชื่อสถานที่"],
                    "เวลาที่ถึง (ETA)": curr_time.strftime("%H:%M"),
                    "พิกัด": f"{loc['Lat']}, {loc['Lon']}"
                })
                curr_time += timedelta(seconds=SERVICE_TIME_SEC)

            st.dataframe(pd.DataFrame(schedule), use_container_width=True)

            # --- Export Tool ---
            st.subheader("📥 ดาวน์โหลดใบงาน")
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='openpyxl') as writer:
                pd.DataFrame(schedule).to_excel(writer, index=False, sheet_name='WorkOrder')
            st.download_button(
                label="ดาวน์โหลดไฟล์ Excel สำหรับคนขับรถ",
                data=buf.getvalue(),
                file_name=f"MilkRun_Order_{datetime.now().strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
    else:
        st.error("❌ ไม่สามารถหาเส้นทางที่เหมาะสมได้")
