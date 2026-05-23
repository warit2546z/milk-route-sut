import streamlit as st
import math
import requests
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
import urllib3
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from folium import plugins
from streamlit_folium import st_folium
import pandas as pd
import io

# 🔒 ปิดการแจ้งเตือนความปลอดภัยเพื่อเจาะทะลุ SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# ฟังก์ชันดึงราคาน้ำมัน Real-time (Bypass SSL + User-Agent Spoofing)
# ==========================================
@st.cache_data(ttl=21600) 
def fetch_today_oil_price():
    fake_browser_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'th-TH,th;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    try:
        url = "https://api.chnwt.dev/thai-oil-api/latest"
        res = requests.get(url, headers=fake_browser_headers, timeout=5, verify=False) 
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
            if oil_options:
                return oil_options, date_str
    except Exception:
        pass 

    try:
        url_ptt = "https://orapiweb.pttor.com/oilservice/OilPrice.asmx"
        headers_ptt = {
            'Content-Type': 'text/xml; charset=utf-8',
            'SOAPAction': '"http://www.pttor.com/CurrentOilPrice"',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        body = """<?xml version="1.0" encoding="utf-8"?>
        <soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <CurrentOilPrice xmlns="http://www.pttor.com">
              <Language>thai</Language>
            </CurrentOilPrice>
          </soap:Body>
        </soap:Envelope>"""
        
        res_ptt = requests.post(url_ptt, data=body, headers=headers_ptt, timeout=10, verify=False)
        if res_ptt.status_code == 200:
            root = ET.fromstring(res_ptt.text)
            result_node = root.find('.//{http://www.pttor.com}CurrentOilPriceResult')
            
            if result_node is not None and result_node.text:
                inner_xml = ET.fromstring(result_node.text)
                oil_options = {}
                target_types = ["ดีเซล", "แก๊สโซฮอล์ 91", "แก๊สโซฮอล์ 95"]
                
                for data_row in inner_xml.findall('.//DataAccess'):
                    product = data_row.find('PRODUCT').text
                    price = data_row.find('PRICE').text
                    
                    if price and any(target in product for target in target_types):
                        if "Premium" not in product and "พรีเมียม" not in product:
                            oil_options[product] = float(price)
                            
                if oil_options:
                    now_str = datetime.now().strftime("%Y-%m-%d (ต่อตรง PTT)")
                    return oil_options, now_str
    except Exception:
        pass 

    return None, None

# ฟังก์ชันถอดรหัส Polyline จาก Google Maps API
def decode_google_polyline(polyline_str):
    index, lat, lng = 0, 0, 0
    coordinates = []
    changes = {'latitude': 0, 'longitude': 0}
    while index < len(polyline_str):
        for component in ['latitude', 'longitude']:
            shift, result = 0, 0
            while True:
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1f) << shift
                shift += 5
                if not byte >= 0x20:
                    break
            if (result & 1):
                changes[component] = ~(result >> 1)
            else:
                changes[component] = (result >> 1)
        lat += changes['latitude']
        lng += changes['longitude']
        coordinates.append((lat / 100000.0, lng / 100000.0))
    return coordinates

# ฟังก์ชันสร้างไฟล์ KML สำหรับ Export เส้นทางไปใช้ต่อใน Google Earth
def generate_kml(route_results):
    kml_header = '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n  <Document>\n    <name>Milk Run Optimized Routes</name>\n'
    kml_footer = '  </Document>\n</kml>'
    
    kml_body = ""
    for rr in route_results:
        kml_body += f'    <Placemark>\n      <name>เส้นทาง {rr["car_name"]}</name>\n'
        kml_body += '      <LineString>\n        <tessellate>1</tessellate>\n        <coordinates>\n'
        for lat, lon in rr['polyline_points']:
            kml_body += f'          {lon},{lat},0\n'
        kml_body += '        </coordinates>\n      </LineString>\n    </Placemark>\n'
        
    return kml_header + kml_body + kml_footer

# ฟังก์ชัน Haversine สำรองกรณีหาเส้นทางจริงไม่เจอ
def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

def time_to_min(t_str):
    try:
        h, m = map(int, str(t_str).split(':'))
        return h * 60 + m
    except: return None 

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run Optimization", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนเส้นทางขนส่งนม (VRP Payload Monitor)")
st.markdown("ระบบวิเคราะห์เส้นทางอัจฉริยะ (Routes API v2 + Optimization Sandbox)")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("Google Maps API Key", value="YOUR_GOOGLE_MAPS_API_KEY", type="password")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:00", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ราคาน้ำมัน")
    oil_data, update_date = fetch_today_oil_price()
    if oil_data:
        st.success(f"อัปเดตล่าสุด: {update_date}")
        oil_list = list(oil_data.keys())
        default_oil_idx = 0
        for i, name in enumerate(oil_list):
            if "ดีเซล" in name:
                default_oil_idx = i
                break
        selected_oil = st.selectbox("เลือกชนิดน้ำมัน", oil_list, index=default_oil_idx)
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", value=float(oil_data[selected_oil]), step=0.5, format="%.2f")
    else:
        st.warning("⚠️ ดึงข้อมูลไม่ได้ ใช้ราคาประเมิน")
        THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=35.0, step=0.5, format="%.2f")
    
    st.header("🚚 จำนวนและประเภทรถ")
    col1, col2 = st.columns(2)
    with col1:
        num_pickup = st.number_input("รถกระบะ (คัน)", min_value=0, value=0, step=1)
        num_4w = st.number_input("บรรทุก 4 ล้อ (คัน)", min_value=0, value=0, step=1)
    with col2:
        num_box = st.number_input("กระบะตู้ทึบ (คัน)", min_value=0, value=1, step=1)
        num_6w = st.number_input("บรรทุก 6 ล้อ (คัน)", min_value=0, value=0, step=1)

    st.markdown("**⚖️ น้ำหนักสินค้าสูงสุดที่บรรทุกได้จริง (kg) ต่อคัน**")
    col3, col4 = st.columns(2)
    with col3:
        cap_pickup = st.number_input("รถกระบะ", min_value=100, value=1000, step=100, key="cap_p")
        cap_4w = st.number_input("บรรทุก 4 ล้อ", min_value=100, value=2200, step=100, key="cap_4")
    with col4:
        cap_box = st.number_input("กระบะตู้ทึบ", min_value=100, value=1500, step=100, key="cap_b")
        cap_6w = st.number_input("บรรทุก 6 ล้อ", min_value=500, value=9000, step=500, key="cap_6")

    st.markdown("**⛽ อัตราสิ้นเปลืองวิ่ง (km/L) / จอดติด (L/h)**")
    col5, col6 = st.columns(2)
    with col5:
        km_pickup = st.number_input("กระบะ (km/L)", value=12.0)
        id_pickup = st.number_input("กระบะ (L/h)", value=1.2)
        km_4w = st.number_input("4 ล้อ (km/L)", value=8.0)
        id_4w = st.number_input("4 ล้อ (L/h)", value=2.0)
    with col6:
        km_box = st.number_input("ตู้ทึบ (km/L)", value=10.0)
        id_box = st.number_input("ตู้ทึบ (L/h)", value=1.5)
        km_6w = st.number_input("6 ล้อ (km/L)", value=6.0)
        id_6w = st.number_input("6 ล้อ (L/h)", value=2.5)

    # ==========================================
    # 🧠 การตั้งค่าสมองกล (Optimization Sandbox)
    # ==========================================
    st.markdown("---")
    st.header("⚙️ ตั้งค่าสมองกลจัดเส้นทาง")
    
    first_sol_dict = {
        "AUTOMATIC (ให้ระบบเลือกเอง)": routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC,
        "SAVINGS (ประหยัดระยะทางทีละคัน)": routing_enums_pb2.FirstSolutionStrategy.SAVINGS,
        "PARALLEL_SAVINGS (ประหยัดแบบขนาน)": routing_enums_pb2.FirstSolutionStrategy.PARALLEL_SAVINGS,
        "PATH_CHEAPEST_ARC (เพื่อนบ้านที่ใกล้ที่สุด)": routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC,
        "LOCAL_CHEAPEST_INSERTION (แทรกจุดที่คุ้มที่สุด)": routing_enums_pb2.FirstSolutionStrategy.LOCAL_CHEAPEST_INSERTION,
        "GLOBAL_CHEAPEST_ARC (จับคู่จุดใกล้สุดทั้งกระดาน)": routing_enums_pb2.FirstSolutionStrategy.GLOBAL_CHEAPEST_ARC
    }
    
    selected_first_sol_name = st.selectbox(
        "1. อัลกอริทึมร่างเส้นทางตั้งต้น", 
        list(first_sol_dict.keys()),
        index=2 
    )

    metaheuristic_dict = {
        "AUTOMATIC (ให้ระบบเลือกเอง)": routing_enums_pb2.LocalSearchMetaheuristic.AUTOMATIC,
        "GUIDED_LOCAL_SEARCH (เน้นแก้จุดบอด)": routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH,
        "SIMULATED_ANNEALING (จำลองการหลอมโลหะ)": routing_enums_pb2.LocalSearchMetaheuristic.SIMULATED_ANNEALING,
        "TABU_SEARCH (ค้นหาแบบมีข้อห้าม)": routing_enums_pb2.LocalSearchMetaheuristic.TABU_SEARCH,
        "GREEDY_DESCENT (ไต่ลงเขาแบบละโมบ)": routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT
    }

    selected_meta_name = st.selectbox(
        "2. สมองกลปรับปรุงเส้นทาง", 
        list(metaheuristic_dict.keys()),
        index=1 
    )

    active_vehicles = []
    for _ in range(num_pickup): active_vehicles.append({'type': 'รถกระบะ', 'km_l': km_pickup, 'idle': id_pickup, 'max_weight': cap_pickup})
    for _ in range(num_box): active_vehicles.append({'type': 'กระบะตู้ทึบ', 'km_l': km_box, 'idle': id_box, 'max_weight': cap_box})
    for _ in range(num_4w): active_vehicles.append({'type': 'บรรทุก 4 ล้อ', 'km_l': km_4w, 'idle': id_4w, 'max_weight': cap_4w})
    for _ in range(num_6w): active_vehicles.append({'type': 'บรรทุก 6 ล้อ', 'km_l': km_6w, 'idle': id_6w, 'max_weight': cap_6w})

    DEAD_SPACE_RATIO = 0.15 
EMISSION_FACTOR = 2.70757206 

# ==========================================
# 3. จัดการข้อมูลนำเข้า
# ==========================================
st.subheader("📍 นำเข้าข้อมูลจุดจัดส่ง")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel/CSV)", type=["csv", "xlsx"])

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
# 4. ประมวลผล (Optimization Core)
# ==========================================
st.markdown("---")
if st.button("🚀 ประมวลผลเส้นทางและวิเคราะห์เปรียบเทียบ", type="primary", use_container_width=True):
    
    total_vehicles = len(active_vehicles)
    if total_vehicles == 0:
        st.error("❌ กรุณาระบุจำนวนรถอย่างน้อย 1 คัน")
        st.stop()

    for col in ["200cc", "2L", "5L", "Yogurt"]:
        if col in edited_df.columns:
            edited_df[col] = pd.to_numeric(edited_df[col], errors='coerce').fillna(0)

    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        w_200cc = float(row.get("200cc", 0)) * 0.221  
        w_2l = float(row.get("2L", 0)) * 2.12        
        w_5l = float(row.get("5L", 0)) * 5.28        
        w_yogurt = float(row.get("Yogurt", 0)) * 0.070 
        total_weight_kg = w_200cc + w_2l + w_5l + w_yogurt
        demands.append(math.ceil(total_weight_kg * (1.0 + DEAD_SPACE_RATIO)))
    
    total_fleet_capacity = sum([v['max_weight'] for v in active_vehicles])
    if sum(demands) > total_fleet_capacity:
        st.error(f"❌ น้ำหนักของรวม ({sum(demands):,} kg) เกินความจุสินค้าของรถทั้งหมดในลาน ({total_fleet_capacity:,} kg)")
        st.stop()
        
    with st.spinner(f'กำลังดึงตารางระยะทางจริงจาก Google Routes API (v2) สำหรับรถ {total_vehicles} คัน...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        num_points = len(coords)
        dist_matrix = [[0] * num_points for _ in range(num_points)]
        
        routes_url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": API_KEY,
            "X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,status"
        }
        
        waypoints = [{"waypoint": {"location": {"latLng": {"latitude": c[0], "longitude": c[1]}}}} for c in coords]
        payload = {
            "origins": waypoints,
            "destinations": waypoints,
            "travelMode": "DRIVE"
        }
        
        try:
            res = requests.post(routes_url, headers=headers, json=payload, verify=False)
            if res.status_code == 200:
                data = res.json()
                for item in data:
                    i = item.get('originIndex')
                    j = item.get('destinationIndex')
                    if i is not None and j is not None:
                        if 'distanceMeters' in item:
                            dist_matrix[i][j] = item['distanceMeters']
                        elif i == j:
                            dist_matrix[i][j] = 0
                        else:
                            dist_matrix[i][j] = haversine_distance(coords[i], coords[j])
            else:
                st.error(f"❌ Routes API Error: {res.status_code} - โปรดตรวจสอบการจำกัดสิทธิ์ API Key ครับ")
                st.stop()
        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาดทางระบบเครือข่าย: {e}")
            st.stop()

        manager = pywrapcp.RoutingIndexManager(len(coords), total_vehicles, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        routing.AddDimension(transit_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        
        for v in range(total_vehicles):
            time_dim.CumulVar(routing.Start(v)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            time_dim.CumulVar(idx).SetRange(s, 2880)
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100)

        demand_idx = routing.RegisterUnaryTransitCallback(lambda idx: demands[manager.IndexToNode(idx)])
        vehicle_capacities = [int(v['max_weight']) for v in active_vehicles]
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, vehicle_capacities, True, "Capacity")

        # 🎯 อัปเดตพารามิเตอร์การตั้งค่าสมองกลตามที่เลือกจาก UI
        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = first_sol_dict[selected_first_sol_name]
        search_params.local_search_metaheuristic = metaheuristic_dict[selected_meta_name]
        search_params.time_limit.seconds = 5
        solution = routing.SolveWithParameters(search_params)

    if solution:
        all_routes = []
        for vehicle_id in range(total_vehicles):
            index = routing.Start(vehicle_id)
            route_indices = []
            while not routing.IsEnd(index):
                route_indices.append(manager.IndexToNode(index))
                index = solution.Value(routing.NextVar(index))
            route_indices.append(manager.IndexToNode(index)) 
            
            if len(route_indices) > 2:
                loaded_weight = sum([demands[n] for n in route_indices])
                all_routes.append({
                    'v_id': vehicle_id, 
                    'v_info': active_vehicles[vehicle_id], 
                    'indices': route_indices,
                    'loaded_weight': loaded_weight 
                })

        route_results = []
        map_colors = ['#2980B9', '#27AE60', '#8E44AD', '#E67E22', '#C0392B', '#D35400', '#16A085']
        
        total_dist_km, total_cost_thb, total_co2_kg, max_time_sec = 0, 0, 0, 0
        
        for idx, route in enumerate(all_routes):
            indices = route['indices']
            v_info = route['v_info']
            
            chunk_size = 25 
            all_legs = []
            chunk_polyline_coords = []
            api_success = True
            
            for i in range(0, len(indices) - 1, chunk_size):
                chunk_indices = indices[i : i + chunk_size + 1] 
                if len(chunk_indices) < 2:
                    break
                    
                orig_idx = chunk_indices[0]
                dest_idx = chunk_indices[-1]
                wayp_indices = chunk_indices[1:-1]
                
                origin_coord = f"{coords[orig_idx][0]},{coords[orig_idx][1]}"
                destination_coord = f"{coords[dest_idx][0]},{coords[dest_idx][1]}"
                waypoints_list = [f"{coords[n][0]},{coords[n][1]}" for n in wayp_indices]
                waypoints_param = "|".join(waypoints_list) if waypoints_list else ""
                
                gmaps_params = {
                    "origin": origin_coord,
                    "destination": destination_coord,
                    "mode": "driving",
                    "departure_time": "now",
                    "key": API_KEY
                }
                if waypoints_param:
                    gmaps_params["waypoints"] = waypoints_param
                    
                res = requests.get("https://maps.googleapis.com/maps/api/directions/json", params=gmaps_params, verify=False)
                
                if res.status_code == 200:
                    gmaps_data = res.json()
                    if gmaps_data.get('status') == 'OK':
                        g_route = gmaps_data['routes'][0]
                        all_legs.extend(g_route['legs'])
                        
                        for leg in g_route['legs']:
                            for step in leg['steps']:
                                step_coords = decode_google_polyline(step['polyline']['points'])
                                chunk_polyline_coords.extend(step_coords)
                    else:
                        st.error(f"❌ Google Maps Error (คันที่ {idx+1}): {gmaps_data.get('status')} - {gmaps_data.get('error_message', '')}")
                        api_success = False
                        break
                else:
                    st.error(f"❌ API Error รถคันที่ {idx+1}: {res.text}")
                    api_success = False
                    break
            
            if api_success and all_legs:
                dist_meters = sum([leg['distance']['value'] for leg in all_legs])
                duration_seconds = sum([leg.get('duration_in_traffic', leg['duration'])['value'] for leg in all_legs])
                normal_duration = sum([leg['duration']['value'] for leg in all_legs])
                traffic_delay_sec = max(0, duration_seconds - normal_duration)
                
                dist_km = dist_meters / 1000
                fuel_running = dist_km / v_info['km_l']
                fuel_idling = (traffic_delay_sec / 3600) * v_info['idle']
                
                total_fuel_l = fuel_running + fuel_idling
                cost_thb = total_fuel_l * THB_L
                co2_kg = total_fuel_l * EMISSION_FACTOR
                
                total_dist_km += dist_km
                total_cost_thb += cost_thb
                total_co2_kg += co2_kg
                max_time_sec = max(max_time_sec, duration_seconds)
                
                route_results.append({
                    'car_name': f"คันที่ {idx+1} ({v_info['type']})",
                    'legs': all_legs,
                    'polyline_points': chunk_polyline_coords,
                    'indices': indices,
                    'color': map_colors[idx % len(map_colors)],
                    'v_info': v_info,
                    'loaded_weight': route['loaded_weight'] 
                })

        # --- Dashboard ผลลัพธ์รวม ---
        if route_results:
            st.subheader(f"📊 การวิเคราะห์ผลลัพธ์รวม (ใช้งานรถ {len(route_results)} คัน)")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("ระยะทางรวมทั้งหมด", f"{total_dist_km:.2f} กม.")
            c2.metric("ต้นทุนน้ำมันรวม", f"฿{total_cost_thb:.2f}")
            c3.metric("ปริมาณการปล่อย CO2 รวม", f"{total_co2_kg:.2f} kg")
            hh, mm = divmod(max_time_sec // 60, 60)
            c4.metric("เวลาวิ่งนานสุด (คันที่ช้าสุด)", f"{int(hh)} ชม. {int(mm)} นาที")

            st.markdown("---")
            st.subheader("📦 Status การบรรทุกน้ำหนักสินค้าจริงของรถแต่ละคัน")
            for rr in route_results:
                loaded = rr['loaded_weight']
                cap = rr['v_info']['max_weight']
                pct = min(loaded / cap, 1.0)
                st.progress(pct, text=f"🚛 {rr['car_name']}: บรรทุกแล้ว {loaded:,} kg / {cap:,} kg ({int(pct*100)}%)")
            st.markdown("<br>", unsafe_allow_html=True)

            col_map, col_table = st.columns([1.3, 1.7])
            with col_map:
                st.subheader("🗺️ แผนที่เส้นทางขนส่งนม")
                m = folium.Map(location=coords[0], zoom_start=14, control_scale=True)
                folium.TileLayer(
                    tiles='https://mt1.google.com/vt/lyrs=m&x={x}&y={y}&z={z}',
                    attr='Google Maps', name='Google Maps', overlay=False, control=True
                ).add_to(m)

                folium.Marker(coords[0], popup="ฟาร์มต้นทาง", icon=folium.Icon(color='green', icon='home')).add_to(m)
                
                for rr in route_results:
                    plugins.AntPath(
                        locations=rr['polyline_points'], delay=800, dash_array=[15, 30], 
                        color=rr['color'], pulse_color="#FFFFFF", weight=6, opacity=0.8,
                        name=f"{rr['car_name']}"
                    ).add_to(m)
                    
                    for step, n in enumerate(rr['indices'][1:-1]):
                        loc = edited_df.iloc[n]
                        icon_html = f'''<div style="font-size: 10pt; font-weight: bold; color: white; background-color: {rr['color']}; border: 2px solid white; border-radius: 50%; text-align: center; width: 24px; height: 24px; line-height: 20px;">{step+1}</div>'''
                        folium.Marker([loc['Lat'], loc['Lon']], popup=f"{rr['car_name']} | ลำดับ: {step+1}<br>{loc['ชื่อสถานที่']}", icon=folium.DivIcon(html=icon_html)).add_to(m)
                
                folium.LayerControl().add_to(m)
                st_folium(m, width="100%", height=500, returned_objects=[])

            with col_table:
                st.subheader("📋 ตารางวิเคราะห์ลำดับคิวงาน (แยกรายคัน)")
                all_schedules_for_excel = []
                
                for rr in route_results:
                    st.markdown(f"##### 🚛 ใบงาน: {rr['car_name']}")
                    vehicle_schedule = []
                    curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                    v_info = rr['v_info']
                    
                    for i, n in enumerate(rr['indices'][:-1]):
                        t_min, l_dist = 0, 0.0
                        co2_leg, delay_min = 0.0, 0.0
                        traffic_status = "-"
                        loc_data = edited_df.iloc[n]
                        
                        if i > 0:
                            leg_data = rr['legs'][i-1]
                            t_min = math.ceil(leg_data['duration']['value'] / 60)
                            l_dist = leg_data['distance']['value'] / 1000
                            
                            actual_duration = leg_data.get('duration_in_traffic', leg_data['duration'])['value']
                            normal_duration = leg_data['duration']['value']
                            leg_delay_sec = max(0, actual_duration - normal_duration)
                            delay_min = leg_delay_sec / 60
                            
                            traffic_ratio = actual_duration / normal_duration if normal_duration > 0 else 1.0
                            if traffic_ratio >= 1.4:
                                traffic_status = "🔴 ติดขัดหนาแน่น"
                            elif traffic_ratio >= 1.15:
                                traffic_status = "🟡 ชะลอตัว / เข้าซอย"
                            else:
                                traffic_status = "🟢 เดินรถคล่องตัว"
                            
                            f_run = l_dist / v_info['km_l']
                            f_idle = (leg_delay_sec / 3600) * v_info['idle']
                            fuel_used = f_run + f_idle
                            co2_leg = fuel_used * EMISSION_FACTOR
                            
                            curr_time += timedelta(minutes=t_min)
                        
                        maps_url = f"https://www.google.com/maps/search/?api=1&query={loc_data['Lat']},{loc_data['Lon']}"
                        
                        row_data = {
                            "ลำดับ": i if i > 0 else "Start",
                            "สถานที่": loc_data["ชื่อสถานที่"] if i > 0 else "ออกเดินทาง (ฟาร์ม)", 
                            "ถึงเวลา": curr_time.strftime("%H:%M"),
                            "ระยะทาง(กม.)": f"{l_dist:.2f}" if i > 0 else "-",
                            "สภาพจราจร": traffic_status,
                            "รถติด(นาที)": f"{delay_min:.1f}" if i > 0 else "-",
                            "CO2(kg)": f"{co2_leg:.2f}" if i > 0 else "-",
                            "นำทางสำหรับคนขับ": maps_url if i > 0 else None
                        }
                        vehicle_schedule.append(row_data)
                        excel_row = {"คันที่": rr['car_name'], **row_data}
                        all_schedules_for_excel.append(excel_row)
                        
                        curr_time += timedelta(seconds=SERVICE_TIME_SEC)
                    
                    df_vehicle = pd.DataFrame(vehicle_schedule)
                    st.dataframe(
                        df_vehicle, use_container_width=True, hide_index=True,
                        column_config={"นำทางสำหรับคนขับ": st.column_config.LinkColumn("📍 ลิงก์นำทาง", display_text="เปิดแผนที่")}
                    )
                    st.write("") 

                st.write("---")
                df_all_schedules = pd.DataFrame(all_schedules_for_excel)
                dl_col1, dl_col2 = st.columns(2)
                with dl_col1:
                    buf = io.BytesIO()
                    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                        df_all_schedules.to_excel(writer, index=False, sheet_name='MilkRun_Plan')
                    st.download_button("📥 ดาวน์โหลดใบงานรวม (Excel)", buf.getvalue(), "MilkRun_Plan.xlsx", use_container_width=True)
                
                with dl_col2:
                    kml_data = generate_kml(route_results)
                    st.download_button("🗺️ ดาวน์โหลดเส้นทาง (KML)", kml_data, "MilkRun_Routes.kml", mime="application/vnd.google-earth.kml+xml", use_container_width=True)

    else:
        st.error("❌ หาเส้นทางไม่ได้ (เวลาทับซ้อน หรือ น้ำหนักสินค้ารวมเกินกำลังรถที่มีอยู่)")
