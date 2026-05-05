import streamlit as st
import math
import requests
from datetime import datetime, timedelta
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
import folium
from streamlit_folium import st_folium
import pandas as pd
import io  # เพิ่มเข้ามาสำหรับสร้างไฟล์ Excel ดาวน์โหลด

# ==========================================
# 1. ตั้งค่าหน้าเพจ UI
# ==========================================
st.set_page_config(page_title="Milk Run Daily Planner", page_icon="🚚", layout="wide")
st.title("🚚 ระบบจัดเส้นทางนมประจำวัน (As-Is vs To-Be)")
st.markdown("วิเคราะห์เส้นทางอัจฉริยะ พร้อมระบบออกใบงาน Excel & แจ้งเตือน LINE")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    # ✨ เพิ่มช่องใส่ LINE Token
    LINE_TOKEN = st.text_input("LINE Notify Token (ถ้าต้องการส่งเข้าไลน์)", type="password", help="รับได้จาก notify-bot.line.me")
    
    st.header("⏱️ การปฏิบัติงาน")
    DEPART_TIME = st.time_input("เวลาเริ่มออกรถจากฟาร์ม", datetime.strptime("11:20", "%H:%M").time())
    SERVICE_TIME_SEC = st.number_input("เวลาลงนมเฉลี่ยต่อจุด (วินาที)", min_value=0, value=45, step=5)
    
    st.header("⛽ ต้นทุนและพื้นที่")
    THB_L = st.number_input("ราคาน้ำมัน (THB/L)", min_value=1.0, value=40.0, step=0.5, format="%.2f")
    KM_L = st.number_input("อัตราสิ้นเปลือง (km/L)", min_value=1.0, value=12.0, step=0.5, format="%.2f")
    NUM_COOLERS = st.number_input("จำนวนถัง (ใบ)", min_value=1, value=2, step=1)
    ICE_PER_COOLER = st.number_input("น้ำแข็ง/ถัง (L)", min_value=0.0, value=15.0, step=1.0)
    DEAD_SPACE_RATIO_INPUT = st.number_input("พื้นที่เผื่อช่องว่าง (%)", min_value=0.0, value=15.0, step=1.0)
    DEAD_SPACE_RATIO = DEAD_SPACE_RATIO_INPUT / 100

TOTAL_NET_CAPACITY = int((800 - ICE_PER_COOLER) * NUM_COOLERS)
COST_PER_KM = THB_L / KM_L
EMISSION_FACTOR = 2.70757206 

# ==========================================
# 3. จัดการข้อมูลแบบรายวัน
# ==========================================
st.subheader("📍 กำหนดจุดจัดส่งประจำวัน")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel หรือ CSV)", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        
        edited_df = st.data_editor(df, num_rows="dynamic", height=300, use_container_width=True,
            column_config={"Lat": st.column_config.NumberColumn(format="%.7f"), "Lon": st.column_config.NumberColumn(format="%.7f")})
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์ Excel (.xlsx) ที่มีหัวคอลัมน์: ชื่อสถานที่, Lat, Lon, 200cc, 2L, 5L, เริ่มรับได้, ต้องส่งก่อน")
    st.stop()

# ==========================================
# ฟังก์ชันผู้ช่วย
# ==========================================
def time_to_min(t_str):
    try:
        h, m = map(int, str(t_str).split(':'))
        return h * 60 + m
    except:
        return None 

def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

def clean_number(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    try: return float(str(val).replace(',', '').strip())
    except ValueError: return 0.0 

def get_demand_list(df):
    demands = []
    for i, row in df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (clean_number(row.get("200cc")) * 0.2) + (clean_number(row.get("2L")) * 2.0) + (clean_number(row.get("5L")) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    return demands

# ฟังก์ชันส่ง LINE Notify
def send_line_notify(token, message):
    url = 'https://notify-api.line.me/api/notify'
    headers = {'Authorization': f'Bearer {token}'}
    data = {'message': message}
    response = requests.post(url, headers=headers, data=data)
    return response.status_code

# ==========================================
# ประมวลผลเส้นทาง (AI Core)
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณเส้นทางอัจฉริยะ (Run Optimization)", type="primary", use_container_width=True):
    st.session_state['run_opt'] = True

if st.session_state.get('run_opt', False):
    demands = get_demand_list(edited_df)
    total_demand = sum(demands)
    if total_demand > TOTAL_NET_CAPACITY:
        st.error(f"❌ น้ำหนักรวม ({total_demand} L) เกินความจุของรถ ({TOTAL_NET_CAPACITY} L)")
        st.stop()
        
    with st.spinner('กำลังประมวลผลเส้นทางด้วยสมองกล...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        baseline_route = list(range(len(coords))) + [0] 
        baseline_dist_m = sum([dist_matrix[baseline_route[i]][baseline_route[i+1]] for i in range(len(baseline_route)-1)])
        baseline_dist_km = baseline_dist_m / 1000
        baseline_cost = (baseline_dist_km / KM_L) * THB_L
        baseline_emissions = (baseline_dist_km / KM_L) * EMISSION_FACTOR
        
        service_time_min_for_matrix = math.ceil(SERVICE_TIME_SEC / 60)
        time_matrix = [[int((d / 1000) / 30 * 60) + (service_time_min_for_matrix if i != j else 0) for j, d in enumerate(row)] for i, row in enumerate(dist_matrix)]
        
        depart_min = DEPART_TIME.hour * 60 + DEPART_TIME.minute
        time_windows = []
        for _, row in edited_df.iterrows():
            start_min = time_to_min(row.get("เริ่มรับได้"))
            end_min = time_to_min(row.get("ต้องส่งก่อน"))
            if start_min is None: start_min = 0
            if end_min is None: end_min = 2880 
            time_windows.append((start_min, end_min))

        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)

        def time_callback(from_index, to_index): return time_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
        time_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(time_callback_index) 
        
        routing.AddDimension(time_callback_index, 2880, 2880, False, "Time")
        time_dimension = routing.GetDimensionOrDie("Time")
        time_dimension.CumulVar(routing.Start(0)).SetValue(depart_min) 
        
        PENALTY_PER_MINUTE = 100 
        for i, window in enumerate(time_windows):
            index = manager.NodeToIndex(i)
            start_min, end_min = window[0], window[1]
            time_dimension.CumulVar(index).SetRange(start_min, 2880) 
            if i != 0 and end_min < 2880:
                routing.GetDimensionOrDie("Time").SetCumulVarSoftUpperBound(index, end_min, PENALTY_PER_MINUTE)

        def demand_callback(from_index): return demands[manager.IndexToNode(from_index)]
        demand_index = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_index, 0, [int(TOTAL_NET_CAPACITY)], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5 
        solution = routing.SolveWithParameters(search_params)

    if not solution:
        st.error("❌ หาเส้นทางไม่ได้! (น้ำหนักอาจเกิน หรือเวลาซ้อนทับกันเกินไป)")
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
                total_emissions = (total_dist_km / KM_L) * EMISSION_FACTOR
                hours, mins = divmod(route_summary['travelTimeInSeconds'] // 60, 60)
                time_display = f"{hours} ชม. {mins} นาที" if hours > 0 else f"{mins} นาที"
                
                st.success(f"✅ จัดคิวสำเร็จ! (ใช้เวลาเดินทางรวม: {time_display})")
                
                # --- ตารางข้อมูลการจัดส่ง ---
                schedule = []
                line_msg_list = [f"\n🚚 ใบงานเส้นทาง Milk Run\n📅 วันที่: {datetime.now().strftime('%d/%m/%Y')}\n🛣️ ระยะทาง: {total_dist_km:.2f} km | ⛽ ค่าน้ำมัน: ฿{total_cost:.2f}\n\n📍 ลำดับการจัดส่ง:"]
                
                curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                for i, n in enumerate(route_indices):
                    travel_min = 0; leg_dist_km = 0.0
                    loc_name = edited_df.iloc[n]["ชื่อสถานที่"]
                    loc_lat = edited_df.iloc[n]["Lat"]
                    loc_lon = edited_df.iloc[n]["Lon"]
                    google_maps_link = f"https://maps.google.com/?q={loc_lat},{loc_lon}"
                    
                    if i > 0 and i-1 < len(legs):
                        travel_min = math.ceil(legs[i-1]['summary']['travelTimeInSeconds'] / 60)
                        leg_dist_km = legs[i-1]['summary']['lengthInMeters'] / 1000
                        curr_time += timedelta(minutes=travel_min)
                    
                    schedule.append({
                        "ลำดับคิว": i,
                        "ชื่อสถานที่": loc_name,
                        "ระยะทางช่วง (กม.)": f"{leg_dist_km:.2f}" if i > 0 else "-",
                        "คาดว่าจะถึง (ETA)": curr_time.strftime("%H:%M"),
                        "พิกัด GPS": google_maps_link
                    })
                    
                    # ร่างข้อความเตรียมส่ง LINE
                    if i == 0:
                        line_msg_list.append(f"0. {loc_name} (ออกรถ {curr_time.strftime('%H:%M')})")
                    else:
                        line_msg_list.append(f"{i}. {loc_name} (ถึง {curr_time.strftime('%H:%M')})\n📌 แผนที่: {google_maps_link}")
                    
                    if i > 0: curr_time += timedelta(seconds=SERVICE_TIME_SEC)
                
                df_schedule = pd.DataFrame(schedule)

                # --- แสดง UI ตารางและแผนที่ ---
                col_map, col_table = st.columns([1.2, 1.8]) 
                with col_map:
                    m = folium.Map(location=coords[0], zoom_start=12)
                    folium.TileLayer(tiles=f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/{{z}}/{{x}}/{{y}}.png?key={API_KEY}", attr='TomTom', overlay=True).add_to(m)
                    
                    all_points = []
                    for leg in legs:
                        for p in leg['points']: all_points.append([p['latitude'], p['longitude']])
                    folium.PolyLine(all_points, color="#E74C3C", weight=6, opacity=0.8).add_to(m)

                    for i, n in enumerate(route_indices[:-1]):
                        loc_coords = [edited_df.iloc[n]["Lat"], edited_df.iloc[n]["Lon"]]
                        if n == 0:
                            folium.Marker(location=loc_coords, popup="เริ่มต้น", icon=folium.Icon(color='green', icon='home')).add_to(m)
                        else:
                            icon_html = f'''<div style="font-size: 11pt; font-weight: bold; color: white; background-color: #2A80B9; border: 2px solid white; border-radius: 50%; text-align: center; width: 28px; height: 28px; line-height: 24px;">{i}</div>'''
                            folium.Marker(location=loc_coords, popup=f"ลำดับ {i}", icon=folium.DivIcon(html=icon_html, class_name="empty")).add_to(m)
                    st_folium(m, width="100%", height=450, returned_objects=[])

                with col_table:
                    st.subheader("📋 ตารางการจัดส่ง (คลิกลิงก์เพื่อนำทาง)")
                    # ใช้ st.dataframe แบบตั้งค่าให้คลิกลิงก์ได้
                    st.dataframe(
                        df_schedule, 
                        hide_index=True,
                        column_config={"พิกัด GPS": st.column_config.LinkColumn("Google Maps")}
                    )

                    # ✨ โซนเครื่องมือปฏิบัติงาน (Export & LINE)
                    st.markdown("---")
                    st.subheader("🛠️ เครื่องมือปฏิบัติงาน (Operational Tools)")
                    
                    tool_col1, tool_col2 = st.columns(2)
                    
                    # 1. ปุ่มดาวน์โหลด Excel
                    with tool_col1:
                        excel_buffer = io.BytesIO()
                        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                            df_schedule.to_excel(writer, index=False, sheet_name='Route Schedule')
                        excel_data = excel_buffer.getvalue()
                        
                        st.download_button(
                            label="📥 1. ดาวน์โหลดใบงาน (Excel)",
                            data=excel_data,
                            file_name=f"milk_run_schedule_{datetime.now().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True
                        )
                    
                    # 2. ปุ่มส่ง LINE
                    with tool_col2:
                        final_line_msg = "\n".join(line_msg_list)
                        if st.button("📲 2. ส่งใบงานเข้ากลุ่ม LINE", type="primary", use_container_width=True):
                            if LINE_TOKEN:
                                status = send_line_notify(LINE_TOKEN, final_line_msg)
                                if status == 200:
                                    st.success("ส่งแจ้งเตือนเข้า LINE สำเร็จ!")
                                else:
                                    st.error(f"เกิดข้อผิดพลาดในการส่ง LINE (Status: {status})")
                            else:
                                st.warning("กรุณาใส่ LINE Notify Token ที่แถบเมนูด้านซ้ายก่อนครับ")

            else:
                st.error("❌ เชื่อมต่อ TomTom API ไม่สำเร็จ")
        except Exception as e:
            st.error(f"Error แผนที่: {e}")
