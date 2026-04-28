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
st.set_page_config(page_title="Milk Run Advanced", page_icon="🚚", layout="wide")
st.title("🚚 ระบบจัดเส้นทางจัดส่งนม (Advanced Dashboard)")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.header("⏰ กรอบเวลา (Windows)")
    DELIVERY_DATE = st.date_input("วันที่จัดส่ง", datetime.today())
    WINDOWS = st.multiselect("ช่วงเวลาส่งของ (Windows)", 
                             ["11:00-13:00", "16:00-19:00", "21:00-23:00"],
                             default=["11:00-13:00"])
    
    st.header("⛽ ต้นทุนพลังงาน (Fuel Cost)")
    THB_L = st.slider("ราคาน้ำมัน (THB/L)", 20.0, 50.0, 40.0, 0.5)
    KM_L = st.slider("อัตราสิ้นเปลือง (km/L)", 5.0, 20.0, 12.0, 0.5)

    st.header("📦 พื้นที่บรรทุก")
    NUM_COOLERS = st.slider("จำนวนถัง (ใบ)", 1, 5, 2)
    ICE_PER_COOLER = st.slider("น้ำแข็ง/ถัง (L)", 0, 100, 15)
    DEAD_SPACE = 15 

TOTAL_NET_CAPACITY = (800 - ICE_PER_COOLER) * NUM_COOLERS
COST_PER_KM = THB_L / KM_L

c1, c2, c3 = st.columns(3)
c1.metric("ความจุรวมสุทธิ", f"{TOTAL_NET_CAPACITY} ลิตร")
c2.metric("ต้นทุนน้ำมันเฉลี่ย", f"{COST_PER_KM:.2f} บาท/กม.")
c3.info(f"แผนงานวันที่: {DELIVERY_DATE.strftime('%d/%m/%Y')}")

# ==========================================
# 3. ฐานข้อมูลลูกค้า
# ==========================================
locations_dict = {
    0: {"name": "โรงฟาร์ม มทส.", "coords": [14.8890708, 102.0006967], "order": {"200cc": 0, "2L": 0, "5L": 0}},
    1: {"name": "ร้านชัชกร ประตู4", "coords": [14.9014943, 102.009382], "order": {"200cc": 34, "2L": 0, "5L": 0}},
    2: {"name": "โรงเรียนนานาชาติ", "coords": [14.9315226, 102.0256814], "order": {"200cc": 0, "2L": 1, "5L": 0}},
    3: {"name": "ร้านค้าป้าอุไร", "coords": [14.9431989, 102.059023], "order": {"200cc": 0, "2L": 0, "5L": 1}},
    4: {"name": "บ้านลูกค้าซอยสืบศิริ 47", "coords": [14.956479, 102.0596265], "order": {"200cc": 0, "2L": 1, "5L": 0}},
    5: {"name": "ร้านค้าข้างวัดป่าสาละวัน", "coords": [14.9676545, 102.072081], "order": {"200cc": 0, "2L": 3, "5L": 0}},
    6: {"name": "ร้านค้าหลังวัดป่าสาละวัน", "coords": [14.9649547, 102.0762582], "order": {"200cc": 0, "2L": 0, "5L": 3}},
    7: {"name": "ร้านค้าติดถนนเส้นหลัก", "coords": [14.9638058, 102.0666304], "order": {"200cc": 0, "2L": 0, "5L": 2}},
    8: {"name": "โรงแรมสีมาธานี", "coords": [14.9740174, 102.0579373], "order": {"200cc": 0, "2L": 3, "5L": 0}},
    9: {"name": "หอพักอรุณฉายเพลส", "coords": [14.9845378, 102.0781542], "order": {"200cc": 0, "2L": 1, "5L": 0}},
    10: {"name": "ร้านต้นไม้ จอหอ", "coords": [15.028862, 102.1366397], "order": {"200cc": 18, "2L": 0, "5L": 0}},
    11: {"name": "ขนส่งจอหอ", "coords": [15.0477473, 102.1302953], "order": {"200cc": 0, "2L": 0, "5L": 2}},
    12: {"name": "ตลาดแม่สมบูรณ์จอหอ", "coords": [15.0283876, 102.1392388], "order": {"200cc": 15, "2L": 0, "5L": 0}},
    13: {"name": "Laguna Ville (หัวทะเล)", "coords": [14.9931634, 102.1453381], "order": {"200cc": 0, "2L": 0, "5L": 5}},
    14: {"name": "ร้านวินนิตตา", "coords": [14.9790059, 102.1181653], "order": {"200cc": 0, "2L": 0, "5L": 5}},
    15: {"name": "เฮงเฮงน้ำชงโบราณโคตรเข้ม", "coords": [14.9778626, 102.1276947], "order": {"200cc": 0, "2L": 5, "5L": 0}},
    16: {"name": "ซีคิว ภูมอเตอร์", "coords": [14.9779303, 102.1064753], "order": {"200cc": 0, "2L": 1, "5L": 0}},
    17: {"name": "ข้างร้านเอ็กเซ็ง หลังย่าโม", "coords": [14.9746985, 102.1011075], "order": {"200cc": 0, "2L": 1, "5L": 0}},
    18: {"name": "อบจ. (รถขายน้ำเคลื่อนที่)", "coords": [14.9704782, 102.0987304], "order": {"200cc": 0, "2L": 1, "5L": 0}},
    19: {"name": "ลูกค้าสามแยกปัก", "coords": [14.9608835, 102.0521069], "order": {"200cc": 0, "2L": 1, "5L": 0}}
}

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

def calc_required_space(order):
    liquid_vol = (order["200cc"] * 0.2) + (order["2L"] * 2.0) + (order["5L"] * 5.0)
    return math.ceil(liquid_vol * (1.0 + (DEAD_SPACE / 100.0)))

def create_data_model():
    data = {}
    data['distance_matrix'] = [[haversine_distance(locations_dict[i]["coords"], locations_dict[j]["coords"]) for j in range(len(locations_dict))] for i in range(len(locations_dict))]
    data['demands'] = [calc_required_space(locations_dict[i]["order"]) for i in range(len(locations_dict))]
    data['vehicle_capacities'] = [TOTAL_NET_CAPACITY]
    data['num_vehicles'] = 1
    data['depot'] = 0
    return data

# ==========================================
# 5. การประมวลผลเมื่อกดปุ่ม RUN
# ==========================================
st.markdown("---")
if st.button("🚀 ประมวลผล (Run Optimization)", type="primary", use_container_width=True):
    with st.spinner('กำลังใช้สมองกลประมวลผลทางเลือกที่ดีที่สุด...'):
        data = create_data_model()
        manager = pywrapcp.RoutingIndexManager(len(data['distance_matrix']), data['num_vehicles'], data['depot'])
        routing = pywrapcp.RoutingModel(manager)

        def distance_callback(from_index, to_index):
            return data['distance_matrix'][manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
        transit_callback_index = routing.RegisterTransitCallback(distance_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

        def demand_callback(from_index):
            return data['demands'][manager.IndexToNode(from_index)]
        demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_callback_index, 0, data['vehicle_capacities'], True, 'Capacity')

        search_parameters = pywrapcp.DefaultRoutingSearchParameters()
        search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_parameters.time_limit.seconds = 5
        
        solution = routing.SolveWithParameters(search_parameters)

    if not solution:
        st.error("❌ จัดรถไม่สำเร็จ! (น้ำหนักอาจเกินความจุรถ หรือตั้งค่าพื้นที่เผื่อมากเกินไป)")
    else:
        route_indices = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            route_indices.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route_indices.append(manager.IndexToNode(index))

        coords_list = [f"{locations_dict[n]['coords'][0]},{locations_dict[n]['coords'][1]}" for n in route_indices]
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join(coords_list)}/json"

        try:
            response = requests.get(url, params={"key": API_KEY, "computeBestOrder": "false", "routeType": "fastest", "departAt": "now", "travelMode": "car"})
            if response.status_code != 200:
                st.error("❌ เชื่อมต่อ TomTom API ไม่สำเร็จ กรุณาตรวจสอบ API Key")
            else:
                api_data = response.json()
                route = api_data['routes'][0]
                legs = route['legs']

                total_dist_meters = sum([leg['summary']['lengthInMeters'] for leg in legs])
                total_dist_km = total_dist_meters / 1000
                total_fuel_cost = (total_dist_km / KM_L) * THB_L

                st.success("✅ คำนวณสำเร็จ! ตรวจสอบผลลัพธ์การทำงานด้านล่าง")
                col_res1, col_res2, col_res3 = st.columns(3)
                with col_res1:
                    st.metric("ระยะทางขับรถรวม", f"{total_dist_km:.2f} กม.")
                with col_res2:
                    st.metric("ค่าน้ำมันโดยประมาณ", f"฿ {total_fuel_cost:.2f}")
                with col_res3:
                    st.metric("ช่วงเวลาที่จัดส่ง (Windows)", f"{', '.join(WINDOWS)}")

                st.markdown("---")

                col_table, col_map = st.columns([1, 1.5])

                with col_table:
                    st.subheader("📋 ตารางเวลาจัดส่ง (ETA Schedule)")
                    table_data = []

                    if len(WINDOWS) > 0:
                        start_hour = int(WINDOWS[0].split(":")[0])
                        current_dt = datetime.combine(DELIVERY_DATE, datetime.strptime(f"{start_hour}:00", "%H:%M").time())
                    else:
                        current_dt = datetime.combine(DELIVERY_DATE, datetime.strptime("10:30", "%H:%M").time())

                    unload_time = 120 

                    for i, node_idx in enumerate(route_indices):
                        info = locations_dict[node_idx]
                        table_data.append({
                            "ลำดับ": i,
                            "สถานที่": info['name'],
                            "ปริมาตร (L)": data['demands'][node_idx] if node_idx != 0 else 0,
                            "เวลาถึง (ETA)": current_dt.strftime("%H:%M:%S")
                        })
                        if i < len(legs):
                            current_dt += timedelta(seconds=legs[i]['summary']['travelTimeInSeconds'] + unload_time)

                    df = pd.DataFrame(table_data)
                    df.set_index('ลำดับ', inplace=True)
                    st.table(df)

                with col_map:
                    st.subheader("🗺️ แผนที่เส้นทาง (TomTom Traffic)")
                    m = folium.Map(location=locations_dict[0]['coords'], zoom_start=13)
                    folium.TileLayer(tiles=f"
