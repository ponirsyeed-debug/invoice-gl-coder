import io
import json
import fitz  # PyMuPDF for drawing/stamping on PDFs
import pandas as pd
import pypdfium2 as pdfium
import streamlit as st
from google import genai
from google.genai import types
from PIL import Image

st.set_page_config(
    page_title="Universal Invoice GL Coding & PDF Stamper",
    page_icon="📑",
    layout="wide",
)

st.title("📑 Smart Invoice GL Coder & Annotated PDF Generator")
st.caption(
    "Upload any vendor invoice (Sysco, Guest Supply, HD Supply, Utilities,"
    " etc.). The AI extracts GL categories, checks balance math, and stamps the"
    " GL breakdown directly onto your PDF."
)

# API Key handling
api_key = st.secrets.get("GEMINI_API_KEY", None)
if not api_key:
  api_key = st.sidebar.text_input("Gemini API Key", type="password")

if not api_key:
  st.warning("Please configure your GEMINI_API_KEY to continue.")
  st.stop()

client = genai.Client(api_key=api_key)

uploaded_file = st.file_uploader(
    "Upload Vendor Invoice (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"]
)


def get_pdf_images(pdf_bytes):
  images = []
  pdf = pdfium.PdfDocument(pdf_bytes)
  for page in pdf:
    image = page.render(scale=2.0).to_pil()
    images.append(image)
  return images


def stamp_gl_summary_on_pdf(pdf_bytes, data):
  """Stamps a handwritten-style red GL summary block directly on the last page of the PDF."""
  doc = fitz.open(stream=pdf_bytes, filetype="pdf")
  page = doc[-1]  # Stamp on the final summary/total page

  # Bounding box for the summary block on the right-hand margin / footer
  rect = fitz.Rect(
      page.rect.width - 240, page.rect.height - 280, page.rect.width - 20,
      page.rect.height - 30
  )

  # Draw a subtle background box
  page.draw_rect(
      rect, color=(0.8, 0.1, 0.1), fill=(1, 0.96, 0.96), width=1.5
  )

  # Build text content
  lines = ["--- GL SUMMARY ---"]
  for item in data.get("gl_summary", []):
    lines.append(
        f"{item['gl_name'][:14]} {item['gl_code']}: ${item['subtotal']:,.2f}"
    )

  lines.append("-------------------")
  lines.append(f"TOTAL: ${data.get('invoice_total', 0.0):,.2f}")

  text_content = "\n".join(lines)
  page.insert_textbox(
      rect + (8, 8, -8, -8),
      text_content,
      fontsize=9,
      fontname="helv",
      color=(0.75, 0.0, 0.0),
  )

  output_pdf = io.BytesIO()
  doc.save(output_pdf)
  doc.close()
  return output_pdf.getvalue()


if uploaded_file is not None:
  file_bytes = uploaded_file.getvalue()
  is_pdf = uploaded_file.name.lower().endswith(".pdf")

  if is_pdf:
    images = get_pdf_images(file_bytes)
  else:
    images = [Image.open(io.BytesIO(file_bytes))]

  # Layout: Left column for raw preview, Right column for processed output
  col_left, col_right = st.columns([1, 1])

  with col_left:
    st.subheader("📄 Uploaded Invoice Preview")
    st.image(
        images[0],
        caption=f"Page 1 of {len(images)}",
        use_container_width=True,
    )

  with col_right:
    st.subheader("⚙️ Processing & GL Assignment")
    if st.button("🚀 Analyze & Generate Coded Breakdown", type="primary"):
      with st.spinner("Analyzing document structure & assigning GL codes..."):
        prompt = """
                You are an expert hospitality & hotel accountant.
                Analyze all pages of this vendor invoice. Extract header data, itemize the purchased lines, and aggregate the subtotals strictly into standard hotel General Ledger (GL) accounts.

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
                    "vendor": "Extracted Vendor Name",
                    "invoice_number": "Invoice / Order #",
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
                            "item_code": "Item SKU / Pack",
                            "description": "Short description",
                            "amount": 0.00,
                            "gl_code": "5301.1",
                            "gl_name": "Dairy"
                        }
                    ]
                }
                """

        contents = [prompt] + images
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )

        data = json.loads(response.text)
        st.session_state["invoice_data"] = data

    if "invoice_data" in st.session_state:
      data = st.session_state["invoice_data"]

      # Top Metrics Cards
      m1, m2, m3 = st.columns(3)
      m1.metric("Vendor", data.get("vendor", "N/A"))
      m2.metric("Invoice #", data.get("invoice_number", "N/A"))
      m3.metric("Total", f"${data.get('invoice_total', 0.0):,.2f}")

      st.markdown("#### 📊 GL Subtotal Summary")
      df_summary = pd.DataFrame(data.get("gl_summary", []))
      st.dataframe(df_summary, use_container_width=True, hide_index=True)

      # Balance Check
      calc_sum = df_summary["subtotal"].sum() if not df_summary.empty else 0.0
      inv_tot = data.get("invoice_total", 0.0)
      if abs(calc_sum - inv_tot) < 0.05:
        st.success(
            f"✅ Math Balanced: Subtotals match Invoice Total (${inv_tot:,.2f})"
        )
      else:
        st.error(
            f"⚠️ Difference of ${abs(calc_sum - inv_tot):,.2f} detected between"
            " items and invoice total."
        )

      # Downloads Section
      st.markdown("---")
      st.markdown("#### 📥 Download Processed Files")

      d_col1, d_col2 = st.columns(2)

      # 1. Stamped PDF Download
      if is_pdf:
        stamped_pdf_bytes = stamp_gl_summary_on_pdf(file_bytes, data)
        d_col1.download_button(
            label="📄 Download Stamped PDF",
            data=stamped_pdf_bytes,
            file_name=f"Stamped_{data.get('invoice_number', 'Invoice')}.pdf",
            mime="application/pdf",
            type="primary",
        )

      # 2. Excel Download
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
