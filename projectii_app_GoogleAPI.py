import streamlit as st
import math
import requests
from datetime import datetime, timedelta
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from folium import plugins
from folium.plugins import FloatImage
from streamlit_folium import st_folium
import pandas as pd
import io

# ==========================================
# ฟังก์ชันถอดรหัสเส้นทางของ Google Maps
# ==========================================
def decode_polyline(polyline_str):
    index, lat, lng = 0, 0, 0
    coordinates = []
    while index < len(polyline_str):
        shift, result = 0, 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20: break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat
        shift, result = 0, 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20: break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng
        coordinates.append((lat / 100000.0, lng / 100000.0))
    return coordinates

# ==========================================
# ฟังก์ชันดึงราคาน้ำมัน Real-time
# ==========================================
@st.cache_data(ttl=21600) 
def fetch_today_oil_price():
    try:
        url = "https://api.chnwt.dev/thai-oil-api/latest"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json()
            ptt_prices = data['response']['stations']['ptt']
            date_str = data['response']['date']
            target_types = ["ดีเซล", "แก๊สโซฮอล์ 91", "แก๊สโซฮอล์ 95"]
            oil_options = {}
            for key, val in ptt_prices.items():
                name = val['name']
                if any(target in name for target in target_types):
                    if "พรีเมียม" not in name and val['price'] and val['price'] != "-":
                        oil_options[name] = float(val['price'])
            return oil_options, date_str
    except Exception:
        pass
    return None, None

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="SUTMR | ระบบจัดเส้นทางหลายคัน", page_icon="🚚", layout="wide")
st.title("SUT Milk Run (SUTMR) - Multi-Vehicle")
st.markdown("วิเคราะห์เส้นทางด้วยสมองกล รองรับการจัดรถหลายคัน (CVRP) และประเมินจราจร Real-time")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("Google Maps API Key", value="", type="password")
    
    st.header("🚚 การจัดการรถขนส่ง (Fleet)")
    # ✨ อัปเดต 1: เพิ่มเมนูเลือกจำนวนรถ
    NUM_VEHICLES = st.number_input("จำนวนรถขนส่ง (คัน)", min_value=1, max_value=10, value=2, step=1)
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ต้นทุนและพื้นที่บรรทุก")
    oil_data, update_date = fetch_today_oil_price()
    if oil_data:
        st.success(f"อัปเดตราคาล่าสุด: {update_date}")
        selected_oil = st.selectbox("เลือกชนิดน้ำมัน", list(oil_data.keys()))
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", value=float(oil_data[selected_oil]), step=0.5, format="%.2f")
    else:
        st.warning("⚠️ ไม่สามารถดึงข้อมูลราคา Real-time ได้")
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=35.0, step=0.5, format="%.2f")
    
    KM_L = st.number_input("อัตราสิ้นเปลือง (km/L)", min_value=1.0, value=10.0, step=0.5, format="%.2f")
    NUM_COOLERS = st.number_input("จำนวนถัง ต่อ 1 คัน (ใบ)", min_value=1, value=2, step=1)
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=75.0, step=1.0)
    DEAD_SPACE_RATIO = 0.15 

TOTAL_NET_CAPACITY = int((450 - ICE_PER_COOLER) * NUM_COOLERS)
EMISSION_FACTOR = 2.70757206 

# ==========================================
# 3. จัดการข้อมูล
# ==========================================
st.subheader("📍 นำเข้าข้อมูลจุดจัดส่ง")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel หรือ CSV)", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        edited_df = st.data_editor(df, num_rows="dynamic", height=250, use_container_width=True)
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์ข้อมูลลูกค้าเพื่อเริ่มการวิเคราะห์")
    st.stop()

# ==========================================
# 4. ประมวลผล (Optimization Core - Multi-Vehicle)
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

def safe_float(val):
    try:
        f = float(val)
        return 0.0 if math.isnan(f) else f
    except: return 0.0

st.markdown("---")
if st.button("🚀 ประมวลผลเส้นทาง (Multi-Vehicle)", type="primary", use_container_width=True):
    if not API_KEY:
        st.error("❌ กรุณาใส่ API Key ก่อนเริ่มทำงาน")
        st.stop()

    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (safe_float(row.get("200cc", 0)) * 0.2) + (safe_float(row.get("2L", 0)) * 2.0) + (safe_float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    # เช็คว่ารถทั้งหมดที่เลือกไว้ บรรทุกพอหรือไม่
    if sum(demands) > (TOTAL_NET_CAPACITY * NUM_VEHICLES):
        st.error(f"❌ น้ำหนักรวมของนม เกินความจุรวมของรถ {NUM_VEHICLES} คัน (ความจุสูงสุด {TOTAL_NET_CAPACITY * NUM_VEHICLES} L)")
        st.stop()
        
    with st.spinner('กำลังให้สมองกล OR-Tools แบ่งโซนและจัดคิวรถแต่ละคัน...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        baseline_km = sum([dist_matrix[i][i+1] for i in range(len(coords)-1)] + [dist_matrix[len(coords)-1][0]]) / 1000
        
        # ✨ อัปเดต 2: ตั้งค่า Manager ให้มีรถหลายคันตามตัวแปร NUM_VEHICLES
        manager = pywrapcp.RoutingIndexManager(len(coords), NUM_VEHICLES, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        routing.AddDimension(transit_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        
        # ตั้งเวลาเริ่มต้นให้รถ "ทุกคัน"
        for v in range(NUM_VEHICLES):
            time_dim.CumulVar(routing.Start(v)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            time_dim.CumulVar(idx).SetRange(s, 2880)
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100)

        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        
        # ใส่ความจุให้รถทุกคันเท่าๆ กัน
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, [TOTAL_NET_CAPACITY] * NUM_VEHICLES, True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
        solution = routing.SolveWithParameters(search_params)

    if solution:
        # ✨ อัปเดต 3: แยกเส้นทางออกมาทีละคัน
        all_routes = []
        for vehicle_id in range(NUM_VEHICLES):
            route_indices = []
            index = routing.Start(vehicle_id)
            while not routing.IsEnd(index):
                route_indices.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            route_indices.append(manager.IndexToNode(index)) # เพิ่มจุดกลับฟาร์ม
            
            # ถ้ารถคันนี้ได้วิ่งงาน (มีลูกค้ามากกว่า 0) ค่อยเอาไปดึง Google API
            if len(route_indices) > 2:
                all_routes.append(route_indices)

        with st.spinner('กำลังดึงข้อมูลแผนที่และรถติด Real-time ของรถแต่ละคัน...'):
            fleet_total_dist_meters = 0
            fleet_total_time_seconds = 0
            api_success = True
            api_error_msg = ""
            
            # เก็บข้อมูลแยกคันเพื่อเอาไปวาดตารางและแผนที่
            vehicle_map_data = [] 
            
            # วนลูปยิง API ทีละคัน
            for v_id, route_indices in enumerate(all_routes):
                v_points = []
                v_legs = []
                start_idx = 0
                
                while start_idx < len(route_indices) - 1:
                    end_idx = min(start_idx + 26, len(route_indices) - 1)
                    chunk_indices = route_indices[start_idx : end_idx + 1]
                    origin = f"{coords[chunk_indices[0]][0]},{coords[chunk_indices[0]][1]}"
                    destination = f"{coords[chunk_indices[-1]][0]},{coords[chunk_indices[-1]][1]}"
                    waypoints_list = [f"{coords[n][0]},{coords[n][1]}" for n in chunk_indices[1:-1]]
                    waypoints_str = "optimize:false|" + "|".join(waypoints_list) if waypoints_list else ""
                    
                    url = "https://maps.googleapis.com/maps/api/directions/json"
                    params = {"origin": origin, "destination": destination, "waypoints": waypoints_str, "mode": "driving", "key": API_KEY, "language": "th", "departure_time": "now"}
                    res = requests.get(url, params=params)
                    
                    if res.status_code == 200:
                        data = res.json()
                        if data.get('status') == 'OK':
                            route_data = data['routes'][0]
                            v_legs.extend(route_data['legs']) 
                            fleet_total_dist_meters += sum([leg['distance']['value'] for leg in route_data['legs']])
                            
                            for leg in route_
