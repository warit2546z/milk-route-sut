# ==========================================
# 3. จัดการข้อมูลแบบรายวัน (ระบบอัปโหลดไฟล์)
# ==========================================
st.subheader("📍 กำหนดจุดจัดส่งประจำวัน (Upload Locations)")

# 1. สร้างปุ่มอัปโหลดไฟล์ รองรับทั้ง CSV และ Excel
uploaded_file = st.file_uploader("📂 อัปโหลดไฟล์รายการจัดส่ง (CSV หรือ Excel)", type=["csv", "xlsx"])

# 2. ตรวจสอบว่ามีการอัปโหลดไฟล์เข้ามาหรือยัง
if uploaded_file is not None:
    try:
        # อ่านไฟล์ตามนามสกุล
        if uploaded_file.name.endswith('.csv'):
            df = pd.read_csv(uploaded_file)
        else:
            df = pd.read_excel(uploaded_file)
        
        st.success("✅ อัปโหลดข้อมูลสำเร็จ! คุณสามารถตรวจสอบและแก้ไขข้อมูลด้านล่างได้ก่อนกดคำนวณ")
        
        # นำข้อมูลที่อัปโหลดมาแสดงใน Data Editor (เพื่อให้ยังสามารถแก้ตัวเลขหน้างานได้นิดหน่อย)
        edited_df = st.data_editor(
            df, 
            num_rows="dynamic", 
            height=300,
            use_container_width=True,
            column_config={
                "Lat": st.column_config.NumberColumn(format="%.7f"),
                "Lon": st.column_config.NumberColumn(format="%.7f"),
            }
        )
    except Exception as e:
        st.error(f"❌ ไม่สามารถอ่านไฟล์ได้ กรุณาตรวจสอบรูปแบบไฟล์: {e}")
        st.stop() # หยุดการทำงานถ้ารูปแบบไฟล์ผิด
else:
    # 3. กรณีที่ยังไม่อัปโหลดไฟล์ ให้แสดงคำแนะนำและตารางตัวอย่าง
    st.info("💡 **คำแนะนำ:** กรุณาอัปโหลดไฟล์ที่มีหัวคอลัมน์ดังนี้: ชื่อสถานที่, Lat, Lon, 200cc, 2L, 5L, เริ่มรับได้, ต้องส่งก่อน (แถวแรกต้องเป็นฟาร์มเสมอ)")
    
    # หยุดการทำงานของโค้ดส่วนที่เหลือจนกว่าจะมีการอัปโหลดไฟล์
    st.stop()
