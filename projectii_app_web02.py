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
st.set_page_config(page_title="Milk Run Daily Planner", page_icon="🚚", layout="wide")
st.title("🚚 ระบบจัดเส้นทางนมประจำวัน (Next-Gen Version)")
st.markdown("วิเคราะห์เส้นทางอัจฉริยะ พร้อมระบบ Messaging API และ Soft Time Windows")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
    st.subheader("📲 LINE Messaging API")
    LINE_ACCESS_TOKEN = st.text_input("Channel Access Token", type="password")
    LINE_USER_ID = st.text_input("Your User ID (หรือ Group ID)")
    
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
EMISSION_FACTOR = 2.70757206 

# ==========================================
# 3. จัดการข้อมูล
# ==========================================
st.subheader("📍 กำหนดจุดจัดส่งประจำวัน")
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (Excel หรือ CSV)", type=["csv", "xlsx"])

if uploaded_file is not None:
    try:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
        edited_df = st.data_editor(df, num_rows="dynamic", height=300, use_container_width=True)
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้: {e}")
        st.stop()
else:
    st.info("💡 กรุณาอัปโหลดไฟล์เพื่อเริ่มการทำงาน")
    st.stop()

# ==========================================
# ฟังก์ชันผู้ช่วย
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

def clean_number(val):
    if pd.isna(val) or str(val).strip() == "": return 0.0
    try: return float(str(val).replace(',', '').strip())
    except: return 0.0 

# ✨ ฟังก์ชันส่ง LINE ผ่าน Messaging API (Push Message)
def send_line_message(access_token, user_id, text_message):
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {access_token}'}
    payload = {'to': user_id, 'messages': [{'type': 'text', 'text': text_message}]}
    res = requests.post(url, headers=headers, json=payload)
    return res.status_code

# ==========================================
# 4. ประมวลผล (AI Core)
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณเส้นทางและวิเคราะห์เปรียบเทียบ", type="primary", use_container_width=True):
    st.session_state['run_opt'] = True

if st.session_state.get('run_opt', False):
    demands = []
    for i, row in edited_df.iterrows():
        if i == 0: demands.append(0); continue
        vol = (clean_number(row.get("200cc")) * 0.2) + (clean_number(row.get("2L")) * 2.0) + (clean_number(row.get("5L")) * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    
    if sum(demands) > TOTAL_NET_CAPACITY:
        st.error(f"❌ น้ำหนักรวมเกินความจุรถ ({TOTAL_NET_CAPACITY} L)")
        st.stop()
        
    with st.spinner('กำลังคำนวณเส้นทางที่ดีที่สุด...'):
        coords = edited_df[['Lat', 'Lon']].values.tolist()
        dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
        
        # As-Is calculation
        baseline_dist_km = sum([dist_matrix[i][i+1] for i in range(len(coords)-1)] + [dist_matrix[len(coords)-1][0]]) / 1000
        
        # OR-Tools Setup
        manager = pywrapcp.RoutingIndexManager(len(coords), 1, 0)
        routing = pywrapcp.RoutingModel(manager)
        
        def time_callback(from_index, to_index):
            d = dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            return int((d / 1000) / 30 * 60) + (math.ceil(SERVICE_TIME_SEC / 60) if from_index != 0 else 0)
        
        transit_callback_index = routing.RegisterTransitCallback(time_callback)
        routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
        
        routing.AddDimension(transit_callback_index, 2880, 2880, False, "Time")
        time_dim = routing.GetDimensionOrDie("Time")
        time_dim.CumulVar(routing.Start(0)).SetValue(DEPART_TIME.hour * 60 + DEPART_TIME.minute)
        
        # Soft Time Windows
        for i, row in edited_df.iterrows():
            idx = manager.NodeToIndex(i)
            s = time_to_min(row.get("เริ่มรับได้")) or 0
            e = time_to_min(row.get("ต้องส่งก่อน")) or 2880
            time_dim.CumulVar(idx).SetRange(s, 2880)
            if i != 0 and e < 2880:
                time_dim.SetCumulVarSoftUpperBound(idx, e, 100)

        def demand_callback(idx): return demands[manager.IndexToNode(idx)]
        demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_callback)
        routing.AddDimensionWithVehicleCapacity(demand_cb_idx, 0, [TOTAL_NET_CAPACITY], True, "Capacity")

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

        # TomTom Routing API
        url = f"https://api.tomtom.com/routing/1/calculateRoute/{':'.join([f'{coords[n][0]},{coords[n][1]}' for n in route_indices])}/json"
        res = requests.get(url, params={"key": API_KEY})
        
        if res.status_code == 200:
            summary = res.json()['routes'][0]['summary']
            legs = res.json()['routes'][0]['legs']
            dist_km = summary['lengthInMeters'] / 1000
            cost = (dist_km / KM_L) * THB_L
            
            # --- Dashboard ---
            st.subheader("📊 ผลการวิเคราะห์เปรียบเทียบ")
            c1, c2, c3 = st.columns(3)
            c1.metric("ระยะทาง (To-Be)", f"{dist_km:.2f} กม.", f"{dist_km - baseline_dist_km:.2f} กม.", delta_color="inverse")
            c2.metric("ค่าน้ำมัน", f"฿{cost:.2f}", f"฿{((dist_km - baseline_dist_km)/KM_L)*THB_L:.2f}", delta_color="inverse")
            c3.metric("CO2 Emission", f"{(dist_km/KM_L)*EMISSION_FACTOR:.2f} kg", f"{((dist_km - baseline_dist_km)/KM_L)*EMISSION_FACTOR:.2f} kg", delta_color="inverse")

            # --- Table & Tools ---
            schedule = []
            line_msg = [f"🚚 แผน Milk Run: {datetime.now().strftime('%d/%m/%y')}\n"]
            curr = datetime.combine(datetime.today(), DEPART_TIME)
            
            for i, n in enumerate(route_indices[:-1]):
                if i > 0: curr += timedelta(minutes=math.ceil(legs[i-1]['summary']['travelTimeInSeconds']/60))
                loc = edited_df.iloc[n]
                maps_link = f"https://www.google.com/maps?q={loc['Lat']},{loc['Lon']}"
                schedule.append({"คิว": i, "สถานที่": loc["ชื่อสถานที่"], "ETA": curr.strftime("%H:%M"), "Link": maps_link})
                line_msg.append(f"{i}. {loc['ชื่อสถานที่']} ({curr.strftime('%H:%M')})\n📍 {maps_link}")
                curr += timedelta(seconds=SERVICE_TIME_SEC)

            st.dataframe(pd.DataFrame(schedule), column_config={"Link": st.column_config.LinkColumn()}, use_container_width=True)

            # --- Export & LINE ---
            col_ex, col_ln = st.columns(2)
            with col_ex:
                buf = io.BytesIO()
                pd.DataFrame(schedule).to_excel(buf, index=False)
                st.download_button("📥 ดาวน์โหลดใบงาน Excel", buf.getvalue(), "milk_run.xlsx", use_container_width=True)
            with col_ln:
                if st.button("📲 ส่งเข้า LINE (Messaging API)", type="primary", use_container_width=True):
                    if LINE_ACCESS_TOKEN and LINE_USER_ID:
                        status = send_line_message(LINE_ACCESS_TOKEN, LINE_USER_ID, "\n".join(line_msg))
                        st.success("ส่งข้อมูลสำเร็จ!") if status == 200 else st.error(f"Error: {status}")
                    else: st.warning("กรุณาใส่ Token และ User ID")
    else:
        st.error("❌ หาเส้นทางไม่ได้")
