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
st.markdown("วิเคราะห์เส้นทางเชิงลึก: รายระยะทาง, การใช้น้ำมัน และการปล่อยก๊าซ CO2 รายจุด")

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
    DEAD_SPACE_RATIO = 0.15 

TOTAL_NET_CAPACITY = int((800 - ICE_PER_COOLER) * NUM_COOLERS)
EMISSION_FACTOR = 2.70757206  # ค่ามาตรฐานสำหรับการปล่อย CO2 ของน้ำมันดีเซล

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
    st.info("💡 กรุณาอัปโหลดไฟล์ที่มีคอลัมน์: ชื่อสถานที่, Lat, Lon, 200cc, 2L, 5L, เริ่มรับได้, ต้องส่งก่อน")
    st.stop()

# ==========================================
# ฟังก์ชันคำนวณ
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

# ==========================================
# 4. ประมวลผล (Optimization Core)
# ==========================================
st.markdown("---")
if st.button("🚀 ประมวลผลเส้นทางและวิเคราะห์เปรียบเทียบ", type="primary", use_container_width=True):
    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (float(row.get("200cc", 0)) * 0.2) + (float(row.get("2L", 0)) * 2.0) + (float(row.get("5L", 0)) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    if sum(demands) > TOTAL_NET_CAPACITY:
        st.error(f"❌ น้ำหนักรวมเกินความจุรถ ({TOTAL_NET_CAPACITY} L)")
        st.stop()
        
    with st.spinner('กำลังค้นหาเส้นทางที่เหมาะสมที่สุด...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        baseline_km = sum([dist_matrix[i][i+1] for i in range(len(coords)-1)] + [dist_matrix[len(coords)-1][0]]) / 1000
        
        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_idx = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)
        
        routing.AddDimension(transit_idx, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.CumulVar(routing.Start(0)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            time_dim.CumulVar(idx).SetRange(s, 2880)
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100)

        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_idx, 0, [TOTAL_NET_CAPACITY], True, "Capacity")

        search_params = pywrapcp.DefaultRoutingSearchParameters()
        search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.AUTOMATIC
        search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
        search_params.time_limit.seconds = 5
        solution = routing.SolveWithParameters(search_params)

    if solution:
        route_indices = []
        index = routing.Start(0)
        while not routing.IsEnd(index):
            route_indices.append(manager.IndexToNode(index))
            index = solution.Value(routing.NextVar(index))
        route_indices.append(0)

        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join([f'{coords[n][0]},{coords[n][1]}' for n in route_indices])}/json"
        res = requests.get(url, params={"key": API_KEY})
        
        if res.status_code == 200:
            route_data = res.json()['routes'][0]
            dist_km = route_data['summary']['lengthInMeters'] / 1000
            cost = (dist_km / KM_L) * THB_L
            
            # --- Dashboard ---
            st.subheader("📊 การวิเคราะห์ผลลัพธ์รวม (Route Summary)")
            c1, c2, c3 = st.columns(3)
            c1.metric("ระยะทาง (To-Be)", f"{dist_km:.2f} กม.", f"{dist_km - baseline_km:.2f} กม.")
            c2.metric("ต้นทุนน้ำมัน", f"฿{cost:.2f}", f"฿{((dist_km - baseline_km)/KM_L)*THB_L:.2f}")
            c3.metric("CO2 ทั้งเที่ยว", f"{(dist_km/KM_L)*EMISSION_FACTOR:.2f} kg", f"{((dist_km - baseline_km)/KM_L)*EMISSION_FACTOR:.2f} kg")

            # --- แผนที่และตาราง ---
            col_map, col_table = st.columns([1.3, 1.7])
            
            with col_map:
                st.subheader("🗺️ แผนที่เส้นทาง")
                m = folium.Map(location=coords[0], zoom_start=12)
                all_points = []
                for leg in route_data['legs']:
                    for p in leg['points']: all_points.append([p['latitude'], p['longitude']])
                folium.PolyLine(all_points, color="#2980B9", weight=6, opacity=0.8).add_to(m)
                
                for i, n in enumerate(route_indices[:-1]):
                    loc = edited_df.iloc[n]
                    if n == 0:
                        folium.Marker([loc['Lat'], loc['Lon']], popup="ฟาร์ม", icon=folium.Icon(color='green', icon='home')).add_to(m)
                    else:
                        icon_html = f'''<div style="font-size: 11pt; font-weight: bold; color: white; background-color: #E74C3C; border: 2px solid white; border-radius: 50%; text-align: center; width: 28px; height: 28px; line-height: 24px;">{i}</div>'''
                        folium.Marker([loc['Lat'], loc['Lon']], popup=f"ลำดับ {i}: {loc['ชื่อสถานที่']}", icon=folium.DivIcon(html=icon_html)).add_to(m)
                st_folium(m, width="100%", height=500, returned_objects=[])

            with col_table:
                st.subheader("📋 ตารางวิเคราะห์ลำดับคิวงานแบบละเอียด")
                schedule = []
                curr_time = datetime.combine(datetime.today(), DEPART_TIME)
                
                for i, n in enumerate(route_indices[:-1]):
                    travel_min = 0
                    leg_dist = 0.0
                    fuel_used = 0.0
                    co2_leg = 0.0
                    
                    if i > 0:
                        leg_summary = route_data['legs'][i-1]['summary']
                        travel_min = math.ceil(leg_summary['travelTimeInSeconds'] / 60)
                        leg_dist = leg_summary['lengthInMeters'] / 1000
                        fuel_used = leg_dist / KM_L
                        co2_leg = fuel_used * EMISSION_FACTOR
                        curr_time += timedelta(minutes=travel_min)
                    
                    schedule.append({
                        "คิว": i,
                        "สถานที่": edited_df.iloc[n]["ชื่อสถานที่"],
                        "เวลาที่ถึง": curr_time.strftime("%H:%M"),
                        "เดินทาง (นาที)": travel_min if i > 0 else "-",
                        "ระยะทาง (กม.)": f"{leg_dist:.2f}" if i > 0 else "-",
                        "น้ำมัน (ลิตร)": f"{fuel_used:.2f}" if i > 0 else "-",
                        "CO2 (kg)": f"{co2_leg:.2f}" if i > 0 else "-"
                    })
                    
                    curr_time += timedelta(seconds=SERVICE_TIME_SEC)
                
                df_schedule = pd.DataFrame(schedule)
                st.dataframe(df_schedule, use_container_width=True, hide_index=True)
                
                # ปุ่มดาวน์โหลด
                st.markdown("---")
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                    df_schedule.to_excel(writer, index=False, sheet_name='MilkRun_Plan')
                st.download_button("📥 ดาวน์โหลดใบงาน Excel (พร้อมข้อมูลวิเคราะห์)", buf.getvalue(), "Detailed_MilkRun_Plan.xlsx", use_container_width=True)
    else:
        st.error("❌ ไม่สามารถหาเส้นทางได้")
