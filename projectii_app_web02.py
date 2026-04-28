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
st.set_page_config(page_title="Milk Run Dynamic Manager", page_icon="🚚", layout="wide")
st.title("🚚 ระบบวางแผนจัดส่งนมรายวัน (Daily Planning Dashboard)")
st.markdown("แก้ไขที่อยู่ พิกัด และยอดสั่งซื้อได้ผ่านตารางด้านล่างนี้ ระบบจะคำนวณเส้นทางใหม่ให้อัตโนมัติ")

# ==========================================
# 2. แผงควบคุมด้านข้าง (Sidebar)
# ==========================================
with st.sidebar:
    st.header("🔑 การเข้าถึงระบบ")
    API_KEY = st.text_input("TomTom API Key", value="X8xbhfCgq1Tp192jy5KinmhP8wguznSu", type="password")
    
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
st.subheader("📍 จัดการข้อมูลลูกค้าและยอดสั่งซื้อ")
st.write("คำแนะนำ: ลำดับที่ 0 ต้องเป็น 'โรงฟาร์ม มทส.' เสมอ | เพิ่ม/ลบ แถวลูกค้าได้อิสระ")

# ข้อมูลตั้งต้น
default_data = [
    {"ชื่อสถานที่": "โรงฟาร์ม มทส.", "Lat": 14.8890708, "Lon": 102.0006967, "200cc (ขวด)": 0, "2L (ถัง)": 0, "5L (แกลลอน)": 0}
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
# 4. ฟังก์ชันคำนวณ (Core Engine)
# ==========================================
def haversine_distance(coord1, coord2):
    lat1, lon1 = coord1; lat2, lon2 = coord2
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = math.sin(math.radians(lat2 - lat1) / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(math.radians(lon2 - lon1) / 2.0) ** 2
    return int(R * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))))

def get_demand_list(df):
    demands = []
    for _, row in df.iterrows():
        # ป้องกันกรณีพิมพ์ช่องจำนวนขวดว่างไว้ ให้มองเป็น 0
        v_200 = float(row["200cc (ขวด)"]) if pd.notna(row["200cc (ขวด)"]) else 0
        v_2L = float(row["2L (ถัง)"]) if pd.notna(row["2L (ถัง)"]) else 0
        v_5L = float(row["5L (แกลลอน)"]) if pd.notna(row["5L (แกลลอน)"]) else 0
        
        vol = (v_200 * 0.2) + (v_2L * 2.0) + (v_5L * 5.0)
        demands.append(math.ceil(vol * (1.0 + DEAD_SPACE_RATIO)))
    return demands

# ==========================================
# 5. ประมวลผลเส้นทาง
# ==========================================
st.markdown("---")
if st.button("🚀 คำนวณเส้นทางจากข้อมูลปัจจุบัน (Run Optimization)", type="primary", use_container_width=True):
    
    # ดักจับ Error: เช็กว่ามีช่อง Lat, Lon ไหนว่างอยู่ไหม
    if edited_df['Lat'].isna().any() or edited_df['Lon'].isna().any():
        st.warning("⚠️ ตรวจพบช่องพิกัด (Lat หรือ Lon) ว่างเปล่า กรุณากรอกให้ครบหรือลบแถวที่ไม่ได้ใช้ออกก่อนครับ")
        st.stop() # หยุดการทำงานตรงนี้ ไม่ให้เกิด Error จอแดง
        
    if len(edited_df) < 2:
        st.error("กรุณาเพิ่มจุดส่งนมอย่างน้อย 1 จุด (นอกเหนือจากฟาร์ม)")
    else:
        with st.spinner('กำลังคำนวณเส้นทางที่ดีที่สุดตามยอดสั่งซื้อจริง...'):
            coords = edited_df[['Lat', 'Lon']].values.tolist()
            dist_matrix = [[haversine_distance(coords[i], coords[j]) for j in range(len(coords))] for i in range(len(coords))]
            demands = get_demand_list(edited_df)
            
            manager = pywrapcp.RoutingIndexManager(len(dist_matrix), 1, 0)
            routing = pywrapcp.RoutingModel(manager)

            def distance_callback(from_index, to_index):
                return dist_matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]
            transit_callback_index = routing.RegisterTransitCallback(distance_callback)
            routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

            def demand_callback(from_index):
                return demands[manager.IndexToNode(from_index)]
            demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
            routing.AddDimensionWithVehicleCapacity(demand_callback_index, 0, [TOTAL_NET_CAPACITY], True, 'Capacity')

            search_parameters = pywrapcp.DefaultRoutingSearchParameters()
            search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
            solution = routing.SolveWithParameters(search_parameters)

        if not solution:
            st.error(f"❌ สินค้าเกินความจุรถ! ยอดรวมพื้นที่นม: {sum(demands)} ลิตร | ความจุนมสุทธิ: {TOTAL_NET_CAPACITY} ลิตร")
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
                res = requests.get(url, params={"key": API_KEY, "routeType": "fastest", "departAt": "now", "travelMode": "car"})
                if res.status_code == 200:
                    api_data = res.json()
                    route_summary = api_data['routes'][0]['summary']
                    total_dist_km = route_summary['lengthInMeters'] / 1000
                    total_cost = (total_dist_km / KM_L) * THB_L

                    st.success(f"✅ คำนวณสำเร็จ! ระยะทางรวม: {total_dist_km:.2f} กม. | ค่าน้ำมัน: ฿{total_cost:.2f}")

                    col_map, col_table = st.columns([1.5, 1])
                    
                    with col_map:
                        st.markdown("**🗺️ แผนที่เส้นทางพร้อมลำดับคิว**")
                        m = folium.Map(location=coords[0], zoom_start=13)
                        folium.TileLayer(tiles=f"https://api.tomtom.com/traffic/map/4/tile/flow/relative0-dark/{{z}}/{{x}}/{{y}}.png?key={API_KEY}", attr='TomTom', overlay=True).add_to(m)
                        
                        all_points = []
                        for leg in api_data['routes'][0]['legs']:
                            for p in leg['points']:
                                all_points.append([p['latitude'], p['longitude']])
                        folium.PolyLine(all_points, color="#E74C3C", weight=6, opacity=0.8).add_to(m)

                        # วาดไอคอนแบบมีตัวเลข
                        for i, n in enumerate(route_indices[:-1]):
                            info = edited_df.iloc[n]
                            loc_name = info["ชื่อสถานที่"]
                            loc_coords = [info["Lat"], info["Lon"]]
                            
                            if n == 0:
                                folium.Marker(
                                    location=loc_coords,
                                    popup=f"จุดเริ่มต้น: {loc_name}",
                                    icon=folium.Icon(color='green', icon='home')
                                ).add_to(m)
                            else:
                                icon_html = f'''
                                <div style="
                                    background-color: #2A80B9;
                                    border: 2px solid white;
                                    border-radius: 50%;
                                    width: 30px;
                                    height: 30px;
                                    display: flex;
                                    justify-content: center;
                                    align-items: center;
                                    color: white;
                                    font-weight: bold;
                                    font-size: 14px;
                                    box-shadow: 0px 2px 5px rgba(0,0,0,0.3);
                                ">{i}</div>
                                '''
                                folium.Marker(
                                    location=loc_coords,
                                    popup=f"ลำดับที่ {i}: {loc_name}",
                                    icon=folium.DivIcon(icon_size=(30, 30), icon_anchor=(15, 15), html=icon_html)
                                ).add_to(m)

                        st_folium(m, width=700, height=500, returned_objects=[])

                    with col_table:
                        st.markdown("**📊 ลำดับคิวและภาระน้ำหนัก**")
                        final_res = []
                        for i, n in enumerate(route_indices):
                            final_res.append({
                                "คิว": i, 
                                "สถานที่": edited_df.iloc[n]["ชื่อสถานที่"], 
                                "ใช้พื้นที่ (L)": demands[n]
                            })
                        # ใช้ st.table เพื่อความเสถียร ไม่เจอบั๊ก ModuleNotFound
                        df_res = pd.DataFrame(final_res)
                        df_res.set_index('คิว', inplace=True)
                        st.table(df_res)
                        
                else:
                    st.error("เชื่อมต่อ TomTom API ไม่ได้ กรุณาเช็ก API Key")
            except Exception as e:
                st.error(f"Error แผนที่: {e}")
