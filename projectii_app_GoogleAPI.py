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
st.set_page_config(page_title="SUTMR | ระบบจัดเส้นทางแบบผสม (HFVRP)", page_icon="🚚", layout="wide")
st.title("SUT Milk Run (SUTMR) - Heterogeneous Fleet")
st.markdown("ระบบวิเคราะห์เส้นทางอัจฉริยะ รองรับรถขนส่งหลายประเภท (กระบะ, 4 ล้อ, 6 ล้อ) พร้อมประเมินจราจร Real-time")

# ==========================================
# 2. ข้อมูลจำเพาะของรถแต่ละประเภท (Profiles)
# ==========================================
# กำหนดค่ามาตรฐานของรถแต่ละประเภท (คุณวริทธิ์สามารถแก้ตัวเลขตรงนี้ได้เลยครับ)
VEHICLE_PROFILES = {
    "pickup": {"name": "รถกระบะ", "coolers": 2, "km_l": 12.0},
    "box_pickup": {"name": "กระบะตู้ทึบ", "coolers": 3, "km_l": 10.0},
    "truck_4w": {"name": "บรรทุก 4 ล้อ", "coolers": 5, "km_l": 8.0},
    "truck_6w": {"name": "บรรทุก 6 ล้อ", "coolers": 10, "km_l": 6.0}
}

# ==========================================
# 3. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("Google Maps API Key", value="", type="password")
    
    st.header("🚚 การจัดทัพรถขนส่ง (Fleet Mix)")
    st.caption("ระบุจำนวนรถแต่ละประเภทที่มีพร้อมใช้งาน")
    c1, c2 = st.columns(2)
    n_pickup = c1.number_input("รถกระบะ", 0, 5, 1)
    n_box = c2.number_input("กระบะตู้ทึบ", 0, 5, 0)
    n_4w = c1.number_input("บรรทุก 4 ล้อ", 0, 5, 0)
    n_6w = c2.number_input("บรรทุก 6 ล้อ", 0, 5, 0)
    
    # สร้าง Fleet Array เพื่อส่งให้ AI
    fleet_mix = []
    for _ in range(n_pickup): fleet_mix.append(VEHICLE_PROFILES["pickup"])
    for _ in range(n_box): fleet_mix.append(VEHICLE_PROFILES["box_pickup"])
    for _ in range(n_4w): fleet_mix.append(VEHICLE_PROFILES["truck_4w"])
    for _ in range(n_6w): fleet_mix.append(VEHICLE_PROFILES["truck_6w"])
    NUM_VEHICLES = len(fleet_mix)
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:00", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ราคาน้ำมันและพื้นที่บรรทุก")
    oil_data, update_date = fetch_today_oil_price()
    if oil_data:
        st.success(f"อัปเดตราคาล่าสุด: {update_date}")
        oil_names = list(oil_data.keys())
        default_idx = next((i for i, name in enumerate(oil_names) if "ดีเซล" in name), 0)
        selected_oil = st.selectbox("เลือกชนิดน้ำมัน", oil_names, index=default_idx)
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", value=float(oil_data[selected_oil]), step=0.5, format="%.2f")
    else:
        st.warning("⚠️ ไม่สามารถดึงข้อมูลราคา Real-time ได้")
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=32.0, step=0.5, format="%.2f")
    
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=75.0, step=1.0)
    DEAD_SPACE_RATIO = 0.15 
    EMISSION_FACTOR = 2.70757206 

# ==========================================
# 4. จัดการข้อมูล
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
# 5. ประมวลผล (Heterogeneous Optimization)
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
if st.button("🚀 ประมวลผลเส้นทางแบบผสม (HFVRP)", type="primary", use_container_width=True):
    if not API_KEY:
        st.error("❌ กรุณาใส่ API Key ก่อนเริ่มทำงาน")
        st.stop()
        
    if NUM_VEHICLES == 0:
        st.error("❌ กรุณาระบุจำนวนรถขนส่งอย่างน้อย 1 คัน ในเมนูด้านซ้ายมือ")
        st.stop()

    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (safe_float(row.get("200cc", 0)) * 0.2) + (safe_float(row.get("2L", 0)) * 2.0) + (safe_float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    # ✨ อัปเดต: คำนวณความจุเฉพาะของรถแต่ละคัน (Dynamic Capacities)
    vehicle_capacities = [int((450 - ICE_PER_COOLER) * v['coolers']) for v in fleet_mix]
    total_fleet_capacity = sum(vehicle_capacities)
    
    if sum(demands) > total_fleet_capacity:
        st.error(f"❌ น้ำหนักนมรวม เกินความจุของรถ {NUM_VEHICLES} คัน (ความจุฟลีทสูงสุด {total_fleet_capacity} L)")
        st.stop()
        
    with st.spinner('กำลังให้สมองกล OR-Tools จัดคิวรถตามขนาดความจุที่ต่างกัน...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        manager = pywrapcp.RoutingIndexManager(len(coords), NUM_VEHICLES, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        routing.AddDimension(transit_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        
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
        
        # ✨ อัปเดต: โยน Array ความจุของรถแต่ละคันไม่เท่ากันเข้าไปให้ AI คิด
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, vehicle_capacities, True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
        solution = routing.SolveWithParameters(search_params)

    if solution:
        all_routes = []
        for vehicle_id in range(NUM_VEHICLES):
            route_indices = []
            index = routing.Start(vehicle_id)
            while not routing.IsEnd(index):
                route_indices.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            route_indices.append(manager.IndexToNode(index)) 
            
            if len(route_indices) > 2:
                all_routes.append({"v_id": vehicle_id, "indices": route_indices})

        with st.spinner('กำลังดึงข้อมูลแผนที่ถนนจริงและสภาพจราจร (แยกตามประเภทรถ)...'):
            fleet_total_dist_meters = 0
            fleet_total_time_seconds = 0
            fleet_total_cost = 0
            fleet_total_co2 = 0
            api_success = True
            api_error_msg = ""
            vehicle_map_data = [] 
            
            for route_obj in all_routes:
                v_id = route_obj["v_id"]
                route_indices = route_obj["indices"]
                v_profile = fleet_mix[v_id] # ดึงโปรไฟล์ (กินน้ำมัน/ความจุ) ของรถคันนี้
                
                v_points = []
                v_legs = []
                start_idx = 0
                v_dist_meters = 0
                v_time_seconds = 0
                
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
                            
                            chunk_dist = sum([leg['distance']['value'] for leg in route_data['legs']])
                            v_dist_meters += chunk_dist
                            
                            for leg in route_data['legs']:
                                traffic_time = leg.get('duration_in_traffic', leg.get('duration'))['value']
                                v_time_seconds += traffic_time
                                for step in leg['steps']:
                                    v_points.extend(decode_polyline(step['polyline']['points']))
                        else:
                            api_success = False
                            api_error_msg = data.get('status')
                            break
                    else:
                        api_success = False
                        break
                    start_idx = end_idx 
                
                # ✨ อัปเดต: คำนวณค่าน้ำมันและ CO2 ของรถคันนี้แยกต่างหาก (เพราะกินน้ำมันไม่เท่ากัน)
                v_dist_km = v_dist_meters / 1000
                v_cost = (v_dist_km / v_profile['km_l']) * THB_L
                v_co2 = (v_dist_km / v_profile['km_l']) * EMISSION_FACTOR
                
                fleet_total_dist_meters += v_dist_meters
                fleet_total_time_seconds += v_time_seconds
                fleet_total_cost += v_cost
                fleet_total_co2 += v_co2
                
                vehicle_map_data.append({
                    "v_id": v_id + 1,
                    "profile": v_profile,
                    "indices": route_indices,
                    "points": v_points,
                    "legs": v_legs
                })

        if api_success:
            dist_km = fleet_total_dist_meters / 1000
            
            st.subheader("📊 สรุปผลการดำเนินงานรวมฟลีทแบบผสม (Mixed Fleet Summary)")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("จำนวนรถที่ใช้วิ่งจริง", f"{len(all_routes)} คัน")
            c2.metric("ระยะทางรวม", f"{dist_km:.2f} กม.")
            c3.metric("ต้นทุนน้ำมันรวม", f"฿{fleet_total_cost:.2f}")
            c4.metric("ปริมาณ CO2 รวม", f"{fleet_total_co2:.2f} kg")

            color_palette = ['#1A73E8', '#E74C3C', '#2ECC71', '#F39C12', '#9B59B6', '#34495E', '#16A085', '#D35400']

            col_map, col_table = st.columns([1.3, 1.7])
            with col_map:
                st.subheader("🗺️ แผนที่แยกโซนประเภทรถ")
                m = folium.Map(location=coords[0], zoom_start=13)
                folium.TileLayer('http://mt0.google.com/vt/lyrs=m&hl=th&x={x}&y={y}&z={z}', attr='Google', name='Google Maps Base').add_to(m)
                
                folium.Marker(coords[0], popup="ฟาร์ม", icon=folium.Icon(color='green', icon='home')).add_to(m)
                
                for v_data in vehicle_map_data:
                    v_color = color_palette[(v_data["v_id"] - 1) % len(color_palette)]
                    plugins.AntPath(locations=v_data["points"], color=v_color, weight=6, delay=1000).add_to(m)
                    
                    for idx_pos, n in enumerate(v_data["indices"][1:-1]): 
                        loc = edited_df.iloc[n]
                        icon_html = f'''<div style="font-size: 11pt; font-weight: bold; color: white; background-color: {v_color}; border: 2px solid white; border-radius: 50%; text-align: center; width: 28px; height: 28px; line-height: 24px; box-shadow: 2px 2px 4px rgba(0,0,0,0.3);">{idx_pos + 1}</div>'''
                        folium.Marker(
                            [loc['Lat'], loc['Lon']], 
                            popup=f"[{v_data['profile']['name']}] คันที่ {v_data['v_id']} | คิว {idx_pos + 1}: {loc['ชื่อสถานที่']}", 
                            icon=folium.DivIcon(html=icon_html)
                        ).add_to(m)
                
                st_folium(m, width="100%", height=500, returned_objects=[])

            with col_table:
                st.subheader("📋 ตารางลำดับงาน (แยกตามคัน/ประเภทรถ)")
                schedule = []
                
                for v_data in vehicle_map_data:
                    curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                    for i, n in enumerate(v_data["indices"][:-1]):
                        if n == 0 and i != 0: continue 
                        
                        t_min, l_dist, max_speed = 0, 0.0, 0.0
                        dominant_road = "-"
                        
                        if i > 0:
                            leg = v_data["legs"][i-1]
                            duration_sec = leg.get('duration_in_traffic', leg.get('duration'))['value']
                            t_min = math.ceil(duration_sec / 60)
                            l_dist = leg['distance']['value'] / 1000
                            
                            road_types = {"ถนนหลัก": 0, "ถนนรอง": 0, "ซอย/รถติด": 0}
                            for step in leg['steps']:
                                s_dist = step['distance']['value']
                                s_dur = step['duration']['value']
                                if s_dur > 0:
                                    s_speed = (s_dist / 1000) / (s_dur / 3600)
                                    if s_speed > max_speed: max_speed = s_speed
                                    if s_speed >= 50: road_types["ถนนหลัก"] += s_dist
                                    elif s_speed >= 25: road_types["ถนนรอง"] += s_dist
                                    else: road_types["ซอย/รถติด"] += s_dist
                            
                            if sum(road_types.values()) > 0:
                                dominant_road = max(road_types, key=road_types.get)
                            curr_time += timedelta(minutes=t_min)
                            
                        maps_url = f"https://www.google.com/maps/dir/?api=1&destination={edited_df.iloc[n]['Lat']},{edited_df.iloc[n]['Lon']}"
                        
                        schedule.append({
                            "ประเภทรถ": v_data['profile']['name'],
                            "คันที่": v_data['v_id'],
                            "คิว": "Start" if i == 0 else i, 
                            "สถานที่": edited_df.iloc[n]["ชื่อสถานที่"], 
                            "เวลาถึง": curr_time.strftime("%H:%M"), 
                            "นำทาง": maps_url if i > 0 else None,
                            "ระยะทาง(กม.)": f"{l_dist:.2f}" if i > 0 else "-", 
                            "เวลา(รถติด)": f"{t_min} นาที" if i > 0 else "-",
                            "ประเภทถนน": dominant_road
                        })
                        
                        if i > 0: curr_time += timedelta(seconds=SERVICE_TIME_SEC)
                
                df_schedule = pd.DataFrame(schedule)
                st.dataframe(
                    df_schedule, use_container_width=True, hide_index=True,
                    column_config={"นำทาง": st.column_config.LinkColumn("📍 นำทาง", display_text="เปิดแผนที่")}
                )
                
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                    df_schedule.to_excel(writer, index=False, sheet_name='HFVRP_Plan')
                st.download_button("📥 ดาวน์โหลดไฟล์ Excel (แยกคัน)", buf.getvalue(), "SUTMR_HFVRP_Plan.xlsx", use_container_width=True)
        else:
            st.error(f"❌ เกิดข้อผิดพลาดจาก Google Maps API: {api_error_msg}")
    else:
        st.error("❌ ไม่สามารถจัดเส้นทางได้ โปรดตรวจสอบเงื่อนไขเวลา ความจุรถ หรือปริมาณงานที่มากเกินไป")
