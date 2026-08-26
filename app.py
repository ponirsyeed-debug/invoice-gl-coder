import io
import json
import fitz  # PyMuPDF
import pandas as pd
import pypdfium2 as pdfium
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image

st.set_page_config(
    page_title="Universal Invoice GL Coder & Visual Annotator",
    page_icon="✍️",
    layout="wide",
)

st.title("✍️ Smart Invoice GL Coder & Visual Markup Annotator")
st.caption(
    "Powered by HEX DEL RIO, LC Chart of Accounts. Analyzes image & text"
    " scans, draws red-ink markup arrows to line items, and stamps the GL"
    " summary on the PDF."
)

# 1. API Key Setup
api_key = st.secrets.get("GEMINI_API_KEY", None)
if not api_key:
  api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

if not api_key:
  st.warning("Please configure your GEMINI_API_KEY to continue.")
  st.stop()

client = genai.Client(api_key=api_key)
ACTIVE_MODEL = "gemini-3.6-flash"

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


# 2. Optimized Image Converter
def get_pdf_images_fast(pdf_bytes):
  images = []
  pdf = pdfium.PdfDocument(pdf_bytes)
  for page in pdf:
    image = page.render(scale=1.2).to_pil()
    images.append(image)
  return images


# 3. AI-Powered Visual Coordinate Annotation Stamper
def stamp_visual_markup_on_pdf(pdf_bytes, data):
  doc = fitz.open(stream=pdf_bytes, filetype="pdf")
  red_color = (0.85, 0.05, 0.05)

  # Draw Red-Ink Arrows on items using AI Vision coordinates
  for item in data.get("items", []):
    page_idx = int(item.get("page_number", 1)) - 1
    if page_idx < 0 or page_idx >= len(doc):
      page_idx = 0

    page = doc[page_idx]
    w = page.rect.width
    h = page.rect.height

    box = item.get("box_2d", None)
    if box and len(box) == 4:
      ymin, xmin, ymax, xmax = box
      x_start = (xmax / 1000.0) * w
      y_mid = ((ymin + ymax) / 2000.0) * h

      # Keep arrow within page bounds
      if x_start + 45 > w:
        x_start = w - 50

      p_start = fitz.Point(x_start + 3, y_mid)
      p_mid = fitz.Point(x_start + 18, y_mid - 2)
      p_end = fitz.Point(x_start + 32, y_mid)

      # Draw polyline pen arrow
      page.draw_polyline([p_start, p_mid, p_end], color=red_color, width=1.3)
      page.draw_line(
          fitz.Point(p_end.x - 4, p_end.y - 3), p_end, color=red_color, width=1.3
      )
      page.draw_line(
          fitz.Point(p_end.x - 4, p_end.y + 3), p_end, color=red_color, width=1.3
      )

      # Write GL Code in bold red pen ink
      label = (
          f"{item.get('gl_code', '')} ({str(item.get('gl_name', ''))[:8]})"
      )
      page.insert_text(
          fitz.Point(p_end.x + 3, y_mid + 3),
          label,
          fontsize=7.5,
          fontname="Courier-Bold",
          color=red_color,
      )

  # Draw handwritten GL Summary Block on last page
  last_page = doc[-1]
  pw = last_page.rect.width
  ph = last_page.rect.height

  summary_rect = fitz.Rect(pw - 230, ph - 260, pw - 15, ph - 20)
  last_page.draw_rect(
      summary_rect, color=red_color, fill=(1, 0.96, 0.96), width=1.4
  )

  summary_lines = ["-- GL ACCOUNTING SUMMARY --"]
  for item in data.get("gl_summary", []):
    summary_lines.append(
        f"{str(item['gl_name'])[:14]} {item['gl_code']}:"
        f" ${float(item['subtotal']):,.2f}"
    )
  summary_lines.append("----------------------------")
  summary_lines.append(f"TOTAL: ${float(data.get('invoice_total', 0.0)):,.2f}")

  last_page.insert_textbox(
      summary_rect + (6, 6, -6, -6),
      "\n".join(summary_lines),
      fontsize=8,
      fontname="Courier-Bold",
      color=red_color,
  )

  output_pdf = io.BytesIO()
  doc.save(output_pdf)
  doc.close()
  return output_pdf.getvalue()


# 4. UI Layout & Execution
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
    st.info(f"📑 {total_pages} Page(s) ready for fast visual analysis.")

    if st.button("⚡ Fast Analyze & Code", type="primary"):
      with st.spinner(f"Detecting items and coordinates on all pages..."):
        prompt = f"""
                You are an expert hospitality & hotel accountant for HEX DEL RIO, LC.
                Analyze all {total_pages} page(s) of this vendor invoice scan. 
                Extract the line items and detect the visual bounding box (box_2d) in normalized coordinates [ymin, xmin, ymax, xmax] (0 to 1000 scale) for each line item on its respective page.

                Official HEX DEL RIO Chart of Accounts:
                - 5301.1: Dairy (Milk, Butter, Cheese, Yogurt, Creamer, Eggs)
                - 5301.2: Protein, Meats etc. (Bacon, Sausage, Ham, Poultry, Patties)
                - 5301.3: Paper & Utensils (Plates, Cups, Cutlery, Straws, Napkins, Bags, Foil, Trash Liners)
                - 5301.4: Cereal, Breads, and Carbs (Bread, Bagels, Muffins, Waffle mix, Cereal, Pastries)
                - 5301.5: Cinnamon Rolls
                - 5301.6: Fruit & Produce (Fresh apples, Bananas, Melons, Potatoes, Veggies)
                - 5301.7: Condiments (Salsa, Jelly, Syrup, Butter cups, Ketchup, Mustard, Mayo, Spices)
                - 5302: Beverage (Dispenser juices, Soda syrup, Tea bags, Cocoa)
                - 5302.1: Coffee (Brewed coffee packs, Coffee beans)
                - 5303: Food & Bev Sales Tax (Sales tax on food/beverage distributor invoices)
                - 5210.1: Cleaning Supplies (Bleach, floor cleaner, degreaser, disinfectants, janitorial)
                - 5210.2: Guest Room - Material (Shampoo, body wash, soap, conditioner, amenities)
                - 5250.1: Chemicals/Soap & Supply (Laundry detergents, softeners)
                - 5250.3: Linen Replacement (Bed sheets, Towels, Mattress pads, Blankets)
                - 6178: Weekly Guest Reception / Social Hour food & beverage
                - 6701.1: Swimming Pool (Pool chemicals, chlorine)
                - 6701.4: Waste Removal (Dumpster, trash pickup)
                - 6701.5: Pest Control
                - 6702.1: Contracted Repairs
                - 6801: Electricity | 6802: Gas | 6803: Water & Sewer
                - 6902.1: Bank Charges

                Return strictly JSON:
                {{
                    "vendor": "Vendor Name",
                    "invoice_number": "Invoice Number",
                    "invoice_date": "YYYY-MM-DD",
                    "invoice_total": 0.00,
                    "gl_summary": [
                        {{
                            "gl_code": "5301.1",
                            "gl_name": "Dairy",
                            "subtotal": 0.00
                        }}
                    ],
                    "items": [
                        {{
                            "page_number": 1,
                            "item_code": "SKU",
                            "description": "Description",
                            "amount": 0.00,
                            "gl_code": "5301.1",
                            "gl_name": "Dairy",
                            "box_2d": [ymin, xmin, ymax, xmax]
                        }}
                    ]
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
              model=ACTIVE_MODEL,
              contents=contents,
              config=types.GenerateContentConfig(
                  response_mime_type="application/json",
                  temperature=0.1,
              ),
          )
          st.session_state["invoice_data"] = json.loads(response.text)
          st.toast(f"⚡ Completed via {ACTIVE_MODEL}!", icon="🚀")
        except Exception as e:
          st.error(f"API Error ({ACTIVE_MODEL}): {e}")

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

      with st.expander("🔍 View Extracted Line Items", expanded=False):
        df_items = pd.DataFrame(data.get("items", []))
        st.dataframe(df_items, use_container_width=True, hide_index=True)

      d_col1, d_col2 = st.columns(2)
      if is_pdf:
        stamped_pdf_bytes = stamp_visual_markup_on_pdf(file_bytes, data)
        d_col1.download_button(
            label="📄 Download Annotated PDF",
            data=stamped_pdf_bytes,
            file_name=f"Annotated_{data.get('invoice_number', 'Invoice')}.pdf",
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
