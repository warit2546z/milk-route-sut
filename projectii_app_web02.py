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
st.markdown("ระบุเวลาที่ลูกค้าสะดวกรับของ (เว้นว่างได้หากส่งตอนไหนก็ได้) และคีย์ยอดสินค้าแยกตามขนาด")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏱️ การปฏิบัติงาน (Operational)")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("10:30", "%H:%M").time())
    SERVICE_TIME = st.slider("เวลาลงนมเฉลี่ยต่อจุด (นาที)", 1, 15, 3)
    
    st.header("⛽ ต้นทุนและพื้นที่")
    THB_L = st.slider("ราคาน้ำมัน (THB/L)", 20.0, 50.0, 40.0, 0.5)
    KM_L = st.slider("อัตราสิ้นเปลือง (km/L)", 5.0, 20.0, 12.0, 0.5)
    NUM_COOLERS = st.slider("จำนวนถัง (ใบ)", 1, 5, 2)
    ICE_PER_COOLER = st.slider("น้ำแข็ง/ถัง (L)", 0, 100, 15)
    DEAD_SPACE_RATIO = st.slider("พื้นที่เผื่อช่องว่าง (%)", 0, 30, 15) / 100

TOTAL_NET_CAPACITY = (800 - ICE_PER_COOLER) * NUM_COOLERS
COST_PER_KM = THB_L / KM_L

# ==========================================
# 3. ส่วนจัดการข้อมูล (Dynamic Data Editor)
# ==========================================
st.subheader("📍 จัดการพิกัด ยอดสินค้า และกรอบเวลา")
st.info("💡 **ทริค:** หากลูกค้าไม่ได้กำหนดเวลาส่ง ให้เว้นช่อง 'เริ่มรับได้' และ 'ต้องส่งก่อน' ให้ว่างไว้ได้เลยครับ")

default_data = [
    {"ชื่อสถานที่": "โรงฟาร์ม มทส.", "Lat": 14.8890708, "Lon": 102.0006967, "200cc (ขวด)": 0, "2L (ถัง)": 0, "5L (แกลลอน)": 0, "เริ่มรับได้": "08:00", "ต้องส่งก่อน": "18:00"}
]

df_init = pd.DataFrame(default_data)

edited_df = st.data_editor(
    df_init, 
    num_rows="dynamic", 
    use_container_width=True,
    column_config={
        "Lat": st.column_config.NumberColumn(format="%.7f"),
        "Lon": st.column_config.NumberColumn(format="%.7f"),
    }
)

# ==========================================
# 4. ฟังก์ชันเบื้องหลัง (Core Engine)
# ==========================================
def time_to_min(t_str):
    try:
        h, m = map(int, str(t_str).split(':'))
        return h * 60 + m
    except:
        return None # คืนค่า None ถ้าแปลงไม่ได้หรือเว้นว่าง

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

def get_demand_list(df):
    demands = []
    for _, row in df.iterrows():
        v_200 = float(row["200cc (ขวด)"]) if pd.notna(row["200cc (ขวด)"]) and str(row["200cc (ขวด)"]).strip() != "" else 0
        v_2L = float(row["2L (ถัง)"]) if pd.notna(row["2L (ถัง)"]) and str(row["2L (ถัง)"]).strip() != "" else 0
        v_5L = float(row["5L (แกลลอน)"]) if pd.notna(row["5L (แกลลอน)"]) and str(row["5L (แกลลอน)"]).strip() != "" else 0
        
        vol = (v_200 * 0.2) + (v_2L * 2.0) + (v_5L * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    return demands

# ==========================================
# 5. ประมวลผลเส้นทาง
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณเส้นทางและเวลา (Run Optimization)", type="primary", use_container_width=True):
    if edited_df['Lat'].isna().any() or len(edited_df) < 2:
        st.warning("⚠️ ตรวจพบช่องพิกัดว่างเปล่า หรือมีจุดส่งน้อยเกินไป กรุณาตรวจสอบ")
        st.stop()
        
    with st.spinner('สมองกลกำลังจัดคิวตามกรอบเวลาและความจุรถ...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        # ประมาณการเวลาเดินทางเบื้องต้น (ความเร็วเฉลี่ย 30 กม./ชม. = 500 เมตร/นาที)
        time_matrix = [[int((d / 1000) / 30 * 60) + SERVICE_TIME for d in row] for row in dist_matrix]
        
        # -----------------------------
        # จัดการกรอบเวลา (เว้นว่าง = 00:00 - 23:59)
        # -----------------------------
        time_windows = []
        for _, row in edited_df.iterrows():
            start_min = time_to_min(row.get("เริ่มรับได้"))
            end_min = time_to_min(row.get("ต้องส่งก่อน"))
            
            # ถ้าเว้นว่าง หรือกรอกผิดรูปแบบ ให้ตีเป็นส่งตอนไหนก็ได้ (0 - 1440 นาที)
            if start_min is None: start_min = 0
            if end_min is None: end_min = 1440
            
            time_windows.append((start_min, end_min))

        demands = get_demand_list(edited_df)

        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_index, to_index):
            return time_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
        time_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(time_callback_index) # ให้ความสำคัญกับเวลา
        
        routing.AddDimension(time_callback_index, 1440, 1440, False, "Time") # Wait time allowed, Max time 24h
        time_dimension = routing.GetDimensionOrDie("Time")

        for i, window in enumerate(time_windows):
            index = manager.NodeToIndex(i)
            time_dimension.CumulVar(index).SetRange(window[0], window[1])

        def demand_callback(from_index):
            return demands[manager.IndexToNode(from_index)]
        demand_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_index, 0, [TOTAL_NET_CAPACITY], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
        
        solution = routing.SolveWithParameters(search_params)

    if not solution:
        st.error("❌ หาเส้นทางไม่ได้! สาเหตุอาจเกิดจาก: 1. น้ำหนักเกิน 2. ลูกค้าเวลาชนกันส่งไม่ทัน 3. ตั้งเวลาฟาร์มผิดช่วง")
    else:
        route_indices = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            route_indices.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route_indices.append(manager.IndexToNode(index))

        route_coords = [f"{coords[n][0]},{coords[n][1]}" for n in route_indices]
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join(route_coords)}/json"
        
        try:
            res = requests.get(url, params={"key": API_KEY, "routeType": "fastest", "departAt": "now"})
            if res.status_code == 200:
                data = res.json()
                legs = data['routes'][0]['legs']
                
                route_summary = data['routes'][0]['summary']
                total_dist_km = route_summary['lengthInMeters'] / 1000
                total_cost = (total_dist_km / KM_L) * THB_L
                
                st.success(f"✅ จัดคิวสำเร็จ! ระยะทาง: {total_dist_km:.2f} กม. | ต้นทุนประมาณ: ฿{total_cost:.2f}")
                
                col_map, col_table = st.columns([1.5, 1.2])
                with col_map:
                    m = folium.Map(location=coords[0], zoom_start=13)
                    folium.TileLayer(tiles=f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/{{z}}/{{x}}/{{y}}.png?key={API_KEY}", attr='TomTom', overlay=True).add_to(m)
                    
                    all_points = []
                    for leg in legs:
                        for p in leg['points']:
                            all_points.append([p['latitude'], p['longitude']])
                    folium.PolyLine(all_points, color="#E74C3C", weight=6, opacity=0.8).add_to(m)

                    for i, n in enumerate(route_indices[:-1]):
                        info = edited_df.iloc[n]
                        loc_name = info["ชื่อสถานที่"]
                        loc_coords = [info["Lat"], info["Lon"]]
                        
                        if n == 0:
                            folium.Marker(location=loc_coords, popup=f"เริ่มต้น: {loc_name}", icon=folium.Icon(color='green', icon='home')).add_to(m)
                        else:
                            icon_html = f'<div style="background-color: #2A80B9; border: 2px solid white; border-radius: 50%; width: 30px; height: 30px; display: flex; justify-content: center; align-items: center; color: white; font-weight: bold; font-size: 14px; box-shadow: 0px 2px 5px rgba(0,0,0,0.3);">{i}</div>'
                            folium.Marker(location=loc_coords, popup=f"ลำดับที่ {i}: {loc_name}", icon=folium.DivIcon(icon_size=(30, 30), icon_anchor=(15, 15), html=icon_html)).add_to(m)

                    st_folium(m, width="100%", height=500)

                with col_table:
                    st.subheader("📋 กำหนดการและระยะเวลา (Schedule)")
                    schedule = []
                    curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                    
                    for i, n in enumerate(route_indices):
                        travel_min = 0
                        if i > 0 and i-1 < len(legs):
                            travel_min = math.ceil(legs[i-1]['summary']['travelTimeInSeconds'] / 60)
                            curr_time += timedelta(minutes=travel_min)
                        
                        schedule.append({
                            "คิว": i,
                            "สถานที่": edited_df.iloc[n]["ชื่อสถานที่"],
                            "พื้นที่ (L)": demands[n],
                            "ขับรถ (นาที)": travel_min if i > 0 else "-",
                            "ถึงเวลา (ETA)": curr_time.strftime("%H:%M")
                        })
                        
                        if i > 0: # บวกเวลาบริการ (ยกเว้นตอนออกจากฟาร์มครั้งแรก)
                            curr_time += timedelta(minutes=SERVICE_TIME)
                            
                    st.dataframe(pd.DataFrame(schedule), hide_index=True)
            else:
                st.error("❌ เชื่อมต่อ TomTom API ไม่สำเร็จ กรุณาเช็ก API Key")
        except Exception as e:
            st.error(f"Error แผนที่: {e}")
