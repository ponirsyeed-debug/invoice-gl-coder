import io
import json
import time
import fitz  # PyMuPDF
import pandas as pd
import pypdfium2 as pdfium
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image

st.set_page_config(
    page_title="Universal Invoice GL Coder & PDF Stamper",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Fast Invoice GL Coder & Annotated PDF Generator")

# 1. API Key Setup
api_key = st.secrets.get("GEMINI_API_KEY", None)
if not api_key:
  api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

if not api_key:
  st.warning("Please configure your GEMINI_API_KEY to continue.")
  st.stop()

client = genai.Client(api_key=api_key)


# Auto-detect available fast models on this key
@st.cache_data(ttl=3600)
def fetch_active_models(_client_ref):
  active = []
  try:
    for m in _client_ref.models.list():
      name = m.name.replace("models/", "")
      if "flash" in name:
        active.append(name)
  except Exception:
    pass
  if not active:
    active = ["gemini-2.5-flash", "gemini-1.5-flash"]
  return active


detected_models = fetch_active_models(client)
selected_model = st.sidebar.selectbox("Active AI Model", detected_models, index=0)

uploaded_file = st.file_uploader(
    "Upload Vendor Invoice (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"]
)

# Reset state on new file upload
if uploaded_file is not None:
  file_identifier = f"{uploaded_file.name}_{uploaded_file.size}"
  if st.session_state.get("current_file_id") != file_identifier:
    st.session_state["current_file_id"] = file_identifier
    st.session_state.pop("invoice_data", None)
    st.session_state["pdf_page_num"] = 0


# 2. Optimized Image Converter (Fast scale & quality)
def get_pdf_images_fast(pdf_bytes):
  images = []
  pdf = pdfium.PdfDocument(pdf_bytes)
  for page in pdf:
    # scale=1.2 is 3x faster than 2.0 and keeps crisp text for OCR
    image = page.render(scale=1.2).to_pil()
    images.append(image)
  return images


# 3. Fast PDF Stamper
def stamp_gl_summary_and_arrows_on_pdf(pdf_bytes, data):
  doc = fitz.open(stream=pdf_bytes, filetype="pdf")
  red_color = (0.8, 0.05, 0.05)

  for page in doc:
    for item in data.get("items", []):
      search_term = str(item.get("item_code", "")).strip()
      if not search_term or len(search_term) < 3:
        search_term = str(item.get("description", ""))[:12].strip()

      if not search_term:
        continue

      rects = page.search_for(search_term)
      for rect in rects[:1]:
        arrow_start = fitz.Point(rect.x1 + 6, rect.y0 + (rect.height / 2))
        arrow_end = fitz.Point(rect.x1 + 24, rect.y0 + (rect.height / 2))

        page.draw_line(arrow_start, arrow_end, color=red_color, width=1.2)
        page.draw_line(
            fitz.Point(arrow_end.x - 3, arrow_end.y - 2),
            arrow_end,
            color=red_color,
            width=1.2,
        )
        page.draw_line(
            fitz.Point(arrow_end.x - 3, arrow_end.y + 2),
            arrow_end,
            color=red_color,
            width=1.2,
        )

        annotation_text = (
            f"{item.get('gl_code', '')} ({str(item.get('gl_name', ''))[:6]})"
        )
        page.insert_text(
            fitz.Point(arrow_end.x + 4, arrow_end.y + 3),
            annotation_text,
            fontsize=7,
            fontname="helv",
            color=red_color,
        )

  last_page = doc[-1]
  summary_rect = fitz.Rect(
      last_page.rect.width - 240,
      last_page.rect.height - 280,
      last_page.rect.width - 15,
      last_page.rect.height - 20,
  )

  last_page.draw_rect(
      summary_rect, color=red_color, fill=(1, 0.96, 0.96), width=1.5
  )

  summary_lines = ["--- GL SUMMARY ---"]
  for item in data.get("gl_summary", []):
    summary_lines.append(
        f"{str(item['gl_name'])[:12]} {item['gl_code']}: ${float(item['subtotal']):,.2f}"
    )
  summary_lines.append("-------------------")
  summary_lines.append(
      f"TOTAL: ${float(data.get('invoice_total', 0.0)):,.2f}"
  )

  last_page.insert_textbox(
      summary_rect + (6, 6, -6, -6),
      "\n".join(summary_lines),
      fontsize=8,
      fontname="helv",
      color=red_color,
  )

  output_pdf = io.BytesIO()
  doc.save(output_pdf)
  doc.close()
  return output_pdf.getvalue()


# 4. UI Layout
if uploaded_file is not None:
  file_bytes = uploaded_file.getvalue()
  is_pdf = uploaded_file.name.lower().endswith(".pdf")

  if is_pdf:
    images = get_pdf_images_fast(file_bytes)
  else:
    images = [Image.open(io.BytesIO(file_bytes))]

  total_pages = len(images)
  col_left, col_right = st.columns([1, 1])

  with col_left:
    st.subheader("📄 Invoice Preview")
    if total_pages > 1:
      nav_col1, nav_col2 = st.columns(2)
      with nav_col1:
        if st.button("⬅️ Prev") and st.session_state.get("pdf_page_num", 0) > 0:
          st.session_state["pdf_page_num"] -= 1
      with nav_col2:
        if (
            st.button("Next ➡️")
            and st.session_state.get("pdf_page_num", 0) < total_pages - 1
        ):
          st.session_state["pdf_page_num"] += 1

      current_page = st.session_state.get("pdf_page_num", 0)
    else:
      current_page = 0

    st.image(
        images[current_page],
        caption=f"Page {current_page + 1} of {total_pages}",
        use_container_width=True,
    )

  with col_right:
    st.subheader("⚙️ Processing & GL Assignment")
    st.info(f"📑 {total_pages} Page(s) ready for fast analysis.")

    if st.button("⚡ Fast Analyze & Code", type="primary"):
      with st.spinner(f"Analyzing all {total_pages} page(s)..."):
        prompt = f"""
                Analyze all {total_pages} page(s) of this hotel invoice. Extract totals and categorize into GL accounts:
                - 5210.1: Cleaning/Chemicals/Janitorial
                - 5210.2: Guest Room Supplies/Amenities
                - 5210.4: Guest Room Supplies Tax
                - 5250.1: Laundry Chemicals
                - 5250.3: Linen Replacement (Towels, Bedding)
                - 5301.1: Food - Dairy
                - 5301.2: Food - Protein/Meat/Eggs
                - 5301.3: Food - Paper & Utensils
                - 5301.4: Food - Bread/Cereal/Bakery
                - 5301.5: Food - Produce/Fruit
                - 5301.6: Food - Coffee/Condiments/Syrups
                - 5302: Beverages/Juice
                - 6801: Electricity/Utilities
                - 6701.4: Waste Removal
                - 6701.5: Pest Control
                - 6902.1: Bank Charges

                Return strictly JSON:
                {{
                    "vendor": "Vendor Name",
                    "invoice_number": "Invoice Number",
                    "invoice_date": "YYYY-MM-DD",
                    "invoice_total": 0.00,
                    "gl_summary": [{{"gl_code": "5301.1", "gl_name": "Dairy", "subtotal": 0.00}}],
                    "items": [{{"page_number": 1, "item_code": "SKU", "description": "Desc", "amount": 0.00, "gl_code": "5301.1", "gl_name": "Dairy"}}]
                }}
                """

        contents = [prompt]
        for idx, img in enumerate(images, start=1):
          contents.append(f"Page {idx}")
          img_buffer = io.BytesIO()
          img.convert("RGB").save(img_buffer, format="JPEG", quality=70)
          contents.append(
              types.Part.from_bytes(
                  data=img_buffer.getvalue(), mime_type="image/jpeg"
              )
          )

        try:
          response = client.models.generate_content(
              model=selected_model,
              contents=contents,
              config=types.GenerateContentConfig(
                  response_mime_type="application/json",
                  temperature=0.1,
              ),
          )
          st.session_state["invoice_data"] = json.loads(response.text)
          st.toast(f"⚡ Completed via {selected_model}!", icon="🚀")
        except Exception as e:
          st.error(f"API Error ({selected_model}): {e}")

    if "invoice_data" in st.session_state:
      data = st.session_state["invoice_data"]

      m1, m2, m3 = st.columns(3)
      m1.metric("Vendor", data.get("vendor", "N/A"))
      m2.metric("Invoice #", data.get("invoice_number", "N/A"))
      m3.metric("Total", f"${float(data.get('invoice_total', 0.0)):,.2f}")

      st.markdown("#### 📊 GL Subtotals")
      df_summary = pd.DataFrame(data.get("gl_summary", []))
      st.dataframe(df_summary, use_container_width=True, hide_index=True)

      calc_sum = (
          float(df_summary["subtotal"].sum()) if not df_summary.empty else 0.0
      )
      inv_tot = float(data.get("invoice_total", 0.0))
      if abs(calc_sum - inv_tot) < 0.05:
        st.success(f"✅ Balanced (${inv_tot:,.2f})")
      else:
        st.warning(
            f"⚠️ Diff: ${abs(calc_sum - inv_tot):,.2f} (Calc ${calc_sum:,.2f} vs"
            f" Inv ${inv_tot:,.2f})"
        )

      d_col1, d_col2 = st.columns(2)
      if is_pdf:
        stamped_pdf_bytes = stamp_gl_summary_and_arrows_on_pdf(file_bytes, data)
        d_col1.download_button(
            label="📄 Download Annotated PDF",
            data=stamped_pdf_bytes,
            file_name=f"Stamped_{data.get('invoice_number', 'Invoice')}.pdf",
            mime="application/pdf",
            type="primary",
        )

      excel_out = io.BytesIO()
      with pd.ExcelWriter(excel_out, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="GL Summary", index=False)
        pd.DataFrame(data.get("items", [])).to_excel(
            writer, sheet_name="Line Items", index=False
        )

      d_col2.download_button(
          label="📊 Download Excel Breakdown",
          data=excel_out.getvalue(),
          file_name=f"GL_{data.get('invoice_number', 'Invoice')}.xlsx",
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      )
