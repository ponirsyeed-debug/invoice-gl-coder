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
    page_icon="📑",
    layout="wide",
)

st.title("📑 Smart Invoice GL Coder & Annotated PDF Generator")
st.caption(
    "Upload any vendor invoice (Sysco, Guest Supply, HD Supply, Utilities,"
    " etc.). The AI extracts GL categories, verifies math balance, and stamps"
    " red markup arrows with corrected GL codes directly on the PDF."
)

# Sidebar API Key & Model Configuration
api_key = st.secrets.get("GEMINI_API_KEY", None)
if not api_key:
  api_key = st.sidebar.text_input("Enter Gemini API Key", type="password")

if not api_key:
  st.warning("Please configure your GEMINI_API_KEY to continue.")
  st.stop()

client = genai.Client(api_key=api_key)

uploaded_file = st.file_uploader(
    "Upload Vendor Invoice (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"]
)

# -------------------------------------------------------------
# 1. Reset state when a new file is uploaded
# -------------------------------------------------------------
if uploaded_file is not None:
  file_identifier = f"{uploaded_file.name}_{uploaded_file.size}"
  if st.session_state.get("current_file_id") != file_identifier:
    st.session_state["current_file_id"] = file_identifier
    st.session_state.pop("invoice_data", None)
    st.session_state["pdf_page_num"] = 0


def get_pdf_images(pdf_bytes):
  images = []
  pdf = pdfium.PdfDocument(pdf_bytes)
  for page in pdf:
    image = page.render(scale=2.0).to_pil()
    images.append(image)
  return images


# -------------------------------------------------------------
# 2. PDF Annotation with Red Markup Arrows & GL Summary Box
# -------------------------------------------------------------
def stamp_gl_summary_and_arrows_on_pdf(pdf_bytes, data):
  """Stamps a red summary block and draws handwritten-style markup arrows with GL codes next to items."""
  doc = fitz.open(stream=pdf_bytes, filetype="pdf")
  red_color = (0.8, 0.05, 0.05)

  # Stamp line-item annotations on relevant pages
  for page in doc:
    for item in data.get("items", []):
      search_term = item.get("item_code", "").strip()
      if not search_term or len(search_term) < 3:
        # Fallback to search by first 12 characters of description
        search_term = item.get("description", "")[:14].strip()

      if not search_term:
        continue

      rects = page.search_for(search_term)
      for rect in rects[:1]:  # Annotate the first match on page
        arrow_start = fitz.Point(rect.x1 + 10, rect.y0 + (rect.height / 2))
        arrow_end = fitz.Point(rect.x1 + 35, rect.y0 + (rect.height / 2))

        # Draw red markup arrow ->
        page.draw_line(
            arrow_start,
            arrow_end,
            color=red_color,
            width=1.2,
        )
        page.draw_line(
            fitz.Point(arrow_end.x - 4, arrow_end.y - 3),
            arrow_end,
            color=red_color,
            width=1.2,
        )
        page.draw_line(
            fitz.Point(arrow_end.x - 4, arrow_end.y + 3),
            arrow_end,
            color=red_color,
            width=1.2,
        )

        # Write corrected GL code & short name
        annotation_text = f"{item.get('gl_code', '')} ({item.get('gl_name', '')[:8]})"
        page.insert_text(
            fitz.Point(arrow_end.x + 5, arrow_end.y + 3),
            annotation_text,
            fontsize=7.5,
            fontname="helv",
            color=red_color,
        )

  # Stamp final GL Summary Block on the last page
  last_page = doc[-1]
  summary_rect = fitz.Rect(
      last_page.rect.width - 250,
      last_page.rect.height - 290,
      last_page.rect.width - 15,
      last_page.rect.height - 20,
  )

  last_page.draw_rect(
      summary_rect, color=red_color, fill=(1, 0.96, 0.96), width=1.5
  )

  summary_lines = ["--- GL SUMMARY ---"]
  for item in data.get("gl_summary", []):
    summary_lines.append(
        f"{item['gl_name'][:14]} {item['gl_code']}: ${item['subtotal']:,.2f}"
    )
  summary_lines.append("-------------------")
  summary_lines.append(f"TOTAL: ${data.get('invoice_total', 0.0):,.2f}")

  last_page.insert_textbox(
      summary_rect + (8, 8, -8, -8),
      "\n".join(summary_lines),
      fontsize=8.5,
      fontname="helv",
      color=red_color,
  )

  output_pdf = io.BytesIO()
  doc.save(output_pdf)
  doc.close()
  return output_pdf.getvalue()


# -------------------------------------------------------------
# 3. AI Extraction with Fallback Chain
# -------------------------------------------------------------
def call_ai_with_fallback(contents):
  candidate_models = [
      "gemini-2.5-flash",
      "gemini-1.5-flash",
      "gemini-2.0-flash",
      "gemini-3-flash",
  ]
  last_err = None

  for model_name in candidate_models:
    for attempt in range(2):
      try:
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )
        return json.loads(response.text), model_name
      except Exception as e:
        last_err = e
        err_str = str(e)
        if "503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str:
          time.sleep(2)
          continue
        else:
          break

  raise Exception(f"All model endpoints busy or failed. Last error: {last_err}")


# -------------------------------------------------------------
# 4. Main Interface
# -------------------------------------------------------------
if uploaded_file is not None:
  file_bytes = uploaded_file.getvalue()
  is_pdf = uploaded_file.name.lower().endswith(".pdf")

  if is_pdf:
    images = get_pdf_images(file_bytes)
  else:
    images = [Image.open(io.BytesIO(file_bytes))]

  total_pages = len(images)
  col_left, col_right = st.columns([1, 1])

  # --- Left Column: Multi-Page Invoice Viewer ---
  with col_left:
    st.subheader("📄 Uploaded Invoice Viewer")

    if total_pages > 1:
      nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
      with nav_col1:
        if st.button("⬅️ Previous") and st.session_state.get(
            "pdf_page_num", 0
        ) > 0:
          st.session_state["pdf_page_num"] -= 1

      with nav_col3:
        if (
            st.button("Next ➡️")
            and st.session_state.get("pdf_page_num", 0) < total_pages - 1
        ):
          st.session_state["pdf_page_num"] += 1

      current_page = st.session_state.get("pdf_page_num", 0)
      st.slider(
          "Jump to Page",
          1,
          total_pages,
          current_page + 1,
          key="slider_page",
          on_change=lambda: st.session_state.update(
              {"pdf_page_num": st.session_state.slider_page - 1}
          ),
      )
    else:
      current_page = 0

    st.image(
        images[current_page],
        caption=f"Page {current_page + 1} of {total_pages}",
        use_container_width=True,
    )

  # --- Right Column: Analysis & Output ---
  with col_right:
    st.subheader("⚙️ Processing & GL Assignment")
    if st.button("🚀 Analyze & Code Entire Invoice", type="primary"):
      with st.spinner("Extracting line items and calculating GL codes..."):
        prompt = """
                You are an expert hospitality & hotel accountant.
                Analyze all pages of this vendor invoice. Extract header data, itemize every purchased line item with its exact SKU/code/description and cost, and aggregate category subtotals into hotel General Ledger (GL) accounts.

                Standard Hospitality Chart of Accounts Reference:
                - 5210.1: Cleaning Supplies / Chemicals / Soap & Janitorial
                - 5210.2: Guest Room Supplies / Material / Amenities (shampoo, soap)
                - 5210.4: Guest Room Supplies Sales Tax
                - 5250.1: Laundry Chemicals & Soap
                - 5250.3: Linen Replacement (Towels, Bedding, Mattress Pads, Duvets)
                - 5301.1: Food - Dairy (Milk, Butter, Cheese, Yogurt, Creamer)
                - 5301.2: Food - Protein, Meats, Poultry, Bacon, Sausage, Eggs
                - 5301.3: Food - Paper & Utensils (Cups, Plates, Cutlery, Napkins, Trash Liners)
                - 5301.4: Food - Bread, Cereals, Bakery, Muffins, Waffle mix
                - 5301.5: Food - Produce (Fruit, Apples, Bananas, Potatoes, Veggies)
                - 5301.6: Food - Coffee, Condiments, Syrups, Salsa, Spices, Jelly
                - 5301.7: Food - Social / Guest Reception
                - 5302: Breakfast Beverages / Juice concentrate / Tea
                - 6801: Electricity / Gas / Utilities
                - 6701.4: Waste Removal / Trash Services
                - 6701.5: Pest Control
                - 6902.1: Bank Charges / Late Fees

                Return ONLY valid JSON with this exact structure:
                {
                    "vendor": "Vendor Name",
                    "invoice_number": "Invoice Number",
                    "invoice_date": "YYYY-MM-DD",
                    "invoice_total": 0.00,
                    "gl_summary": [
                        {
                            "gl_code": "5301.1",
                            "gl_name": "Dairy",
                            "subtotal": 0.00
                        }
                    ],
                    "items": [
                        {
                            "item_code": "SKU or Item Code",
                            "description": "Short Description",
                            "amount": 0.00,
                            "gl_code": "5301.1",
                            "gl_name": "Dairy"
                        }
                    ]
                }
                """

        contents = [prompt]
        for img in images:
          img_buffer = io.BytesIO()
          img.convert("RGB").save(img_buffer, format="JPEG", quality=80)
          contents.append(
              types.Part.from_bytes(
                  data=img_buffer.getvalue(),
                  mime_type="image/jpeg",
              )
          )

        try:
          parsed_data, used_model = call_ai_with_fallback(contents)
          st.session_state["invoice_data"] = parsed_data
          st.toast(
              f"Invoice analyzed successfully via {used_model}!", icon="✅"
          )
        except Exception as e:
          st.error(f"Generation error: {e}")

    # Display results only if current invoice has been analyzed
    if "invoice_data" in st.session_state:
      data = st.session_state["invoice_data"]

      # Header metrics
      m1, m2, m3 = st.columns(3)
      m1.metric("Vendor", data.get("vendor", "N/A"))
      m2.metric("Invoice #", data.get("invoice_number", "N/A"))
      m3.metric("Total", f"${data.get('invoice_total', 0.0):,.2f}")

      st.markdown("#### 📊 GL Subtotal Summary")
      df_summary = pd.DataFrame(data.get("gl_summary", []))
      st.dataframe(df_summary, use_container_width=True, hide_index=True)

      # Balance check
      calc_sum = df_summary["subtotal"].sum() if not df_summary.empty else 0.0
      inv_tot = data.get("invoice_total", 0.0)
      if abs(calc_sum - inv_tot) < 0.05:
        st.success(
            f"✅ Math Balanced: Subtotals match Invoice Total (${inv_tot:,.2f})"
        )
      else:
        st.warning(
            f"⚠️ Difference of ${abs(calc_sum - inv_tot):,.2f} detected between"
            " items and invoice total."
        )

      st.markdown("---")
      st.markdown("#### 📥 Download Processed Files")
      d_col1, d_col2 = st.columns(2)

      if is_pdf:
        stamped_pdf_bytes = stamp_gl_summary_and_arrows_on_pdf(file_bytes, data)
        d_col1.download_button(
            label="📄 Download Annotated & Stamped PDF",
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
          file_name=f"GL_Coding_{data.get('invoice_number', 'Invoice')}.xlsx",
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      )
