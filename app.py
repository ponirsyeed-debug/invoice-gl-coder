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
st.caption(
    "100% Comprehensive Coverage: HEX DEL RIO, LC Chart of Accounts. Auto-extracts "
    "multi-page vendor invoices, validates math balance, and stamps an auto-fitted GL review box."
)

# -------------------------------------------------------------
# 1. Multi-Key Pool Setup (Loaded from Secrets)
# -------------------------------------------------------------
def load_api_key_pool():
  keys = []
  raw_keys = st.secrets.get("GEMINI_API_KEYS", "")
  if raw_keys:
    keys.extend([k.strip() for k in raw_keys.split(",") if k.strip()])

  if st.secrets.get("GEMINI_API_KEY"):
    keys.append(st.secrets["GEMINI_API_KEY"].strip())

  for i in range(1, 15):
    k = st.secrets.get(f"GEMINI_API_KEY_{i}")
    if k and k.strip() not in keys:
      keys.append(k.strip())

  return list(dict.fromkeys(keys))


api_keys = load_api_key_pool()

if not api_keys:
  st.error("⚠️ No API keys found. Please add your keys to Streamlit Secrets.")
  st.stop()

ACTIVE_MODEL = "gemini-3.6-flash"

uploaded_file = st.file_uploader(
    "Upload Vendor Invoice (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"]
)

# Auto-reset state on new file upload
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


# 3. Dynamic Auto-Sizing Orientation-Aware GL Summary Stamper
def stamp_gl_summary_on_pdf(pdf_bytes, data):
  doc = fitz.open(stream=pdf_bytes, filetype="pdf")
  red_color = (0.8, 0.05, 0.05)

  last_page = doc[-1]
  rot = last_page.rotation
  w = last_page.rect.width
  h = last_page.rect.height

  summary_lines = ["--- REVIEWED & CODED ---"]
  for item in data.get("gl_summary", []):
    summary_lines.append(
        f"{str(item['gl_name'])[:14]} {item['gl_code']}: ${float(item['subtotal']):,.2f}"
    )
  summary_lines.append("------------------------")
  summary_lines.append(f"TOTAL: ${float(data.get('invoice_total', 0.0)):,.2f}")

  line_count = len(summary_lines)
  font_size = 8
  line_height = 11.5
  padding = 14

  box_w = 210
  box_h = (line_count * line_height) + padding
  margin = 15

  if rot == 0:
    summary_rect = fitz.Rect(
        w - box_w - margin, h - box_h - margin, w - margin, h - margin
    )
  elif rot == 90:
    summary_rect = fitz.Rect(
        w - box_h - margin, margin, w - margin, box_w + margin
    )
  elif rot == 270:
    summary_rect = fitz.Rect(
        margin, h - box_w - margin, box_h + margin, h - margin
    )
  else:  # 180
    summary_rect = fitz.Rect(
        margin, margin, box_w + margin, box_h + margin
    )

  last_page.draw_rect(
      summary_rect, color=red_color, fill=(1, 0.96, 0.96), width=1.2
  )

  last_page.insert_textbox(
      summary_rect + (5, 5, -5, -5),
      "\n".join(summary_lines),
      fontsize=font_size,
      fontname="Courier-Bold",
      color=red_color,
      rotate=rot,
  )

  output_pdf = io.BytesIO()
  doc.save(output_pdf)
  doc.close()
  return output_pdf.getvalue()


# 4. Multi-Key Auto-Rotation Engine with Smart Quota Handling
def execute_with_key_rotation(contents):
  last_err = None

  for key_idx, key in enumerate(api_keys, start=1):
    client = genai.Client(api_key=key)

    try:
      response = client.models.generate_content(
          model=ACTIVE_MODEL,
          contents=contents,
          config=types.GenerateContentConfig(
              response_mime_type="application/json",
              temperature=0.1,
          ),
      )
      return json.loads(response.text), f"{ACTIVE_MODEL} (Key #{key_idx})"
    except Exception as e:
      last_err = e
      err_str = str(e)

      # If daily project quota is exhausted, immediately try the next key from the pool
      if "GenerateRequestsPerDay" in err_str or "quotaValue': '20'" in err_str:
        continue

      # If temporary per-minute burst rate limit, short cooldown retry
      elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
        time.sleep(2.0)
        try:
          response = client.models.generate_content(
              model=ACTIVE_MODEL,
              contents=contents,
              config=types.GenerateContentConfig(
                  response_mime_type="application/json",
                  temperature=0.1,
              ),
          )
          return json.loads(response.text), f"{ACTIVE_MODEL} (Key #{key_idx})"
        except Exception as retry_e:
          last_err = retry_e
          continue
      else:
        continue

  raise Exception(
      f"All configured API keys have hit their daily free limit (20 requests/day per project). "
      f"Please attach a billing account on AI Studio or supply a key from a separate Google account. (Details: {last_err})"
  )


# 5. UI Layout & Multi-Page Execution
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
      with st.spinner(f"Analyzing all {total_pages} page(s) using {ACTIVE_MODEL}..."):
        prompt = f"""
                You are an expert hospitality accountant for HEX DEL RIO, LC[cite: 1, 2].
                Analyze all {total_pages} page(s) of this vendor invoice. Extract every purchased line item, identify the vendor, invoice date, number, total, and categorize each line item strictly into the complete HEX DEL RIO Chart of Accounts (COA)[cite: 1, 2].

                ============================================================
                COMPLETE HEX DEL RIO, LC CHART OF ACCOUNTS REFERENCE:
                ============================================================
                --- 1. FOOD & BEVERAGE COGS (5300 series) ---
                - 5301.1: Dairy (Milk, Butter, Cheese, Yogurt, Creamer, Eggs)[cite: 1, 2]
                - 5301.2: Protein, Meats etc. (Bacon, Sausage, Ham, Poultry, Patties)[cite: 1, 2]
                - 5301.3: Paper & Utensils (Plates, Cups, Cutlery, Straws, Napkins, Bags, Foil, Trash Liners)[cite: 1, 2]
                - 5301.4: Cereal, Breads, and Carbs (Bread, Bagels, Muffins, Waffle mix, Cereal, Pastries, Tortillas)[cite: 1, 2]
                - 5301.5: Cinnamon Rolls[cite: 1, 2]
                - 5301.6: Fruit & Produce (Fresh apples, Bananas, Melons, Potatoes, Veggies, Garnish)[cite: 1, 2]
                - 5301.7: Condiments (Salsa, Jelly, Syrup, Butter cups, Ketchup, Mustard, Mayo, Spices, Dressings)[cite: 1, 2]
                - 5302: Beverage (Juices, Beverage dispenser concentrate, Soda syrup, Tea bags, Cocoa)[cite: 1, 2]
                - 5302.1: Coffee (Brewed coffee packs, Coffee beans)[cite: 1, 2]
                - 5302.2: Cups, Stirs & paper goods[cite: 1, 2]
                - 5302.3: Coffee Creamer[cite: 1, 2]
                - 5302.4: Juice[cite: 1, 2]
                - 5302.5: Cocoa[cite: 1, 2]
                - 5302.6: Water[cite: 1, 2]
                - 5302.7: Tea[cite: 1, 2]
                - 5303: Food & Bev Sales Tax (Sales tax on distributor food/beverage invoices)[cite: 1, 2]
                - 5303.1: Gas charged for delivery (Fuel surcharges, Delivery surcharges)[cite: 1, 2]

                --- 2. ROOMS, HOUSEKEEPING & LAUNDRY (5200 series) ---
                - 5210.1: Cleaning Supplies (Bleach, floor cleaner, degreaser, disinfectants, janitorial supplies)[cite: 1, 2]
                - 5210.2: Guest Room - Material (Shampoo, body wash, bar soap, conditioner, lotions, room amenities)[cite: 1, 2]
                - 5210.3: Operating supplies (Housekeeping caddies, spray bottles, smallware)[cite: 1, 2]
                - 5210.4: Guest Room Supplies Sales Tax[cite: 1, 2]
                - 5250.1: Chemicals/Soap & Supply (Laundry detergents, fabric softeners, laundry bleach)[cite: 1, 2]
                - 5250.2: Laundry Equip Repair & Replace[cite: 1, 2]
                - 5250.3: Linen Replacement (Bed sheets, Pillowcases, Duvet covers, Towels, Mattress pads, Blankets)[cite: 1, 2]

                --- 3. PROPERTY OPERATIONS, MAINTENANCE & UTILITIES (6700 - 6800 series) ---
                - 6701.1: Swimming Pool (Pool chemicals, chlorine, pool maintenance)[cite: 1, 2]
                - 6701.2: Elevators (Elevator maintenance contracts / inspections)[cite: 1, 2]
                - 6701.3: Grounds & Landscape (Landscaping, lawn mowing, tree trimming)[cite: 1, 2]
                - 6701.4: Waste Removal (Dumpster, trash pickup, recycling, waste services)[cite: 1, 2]
                - 6701.5: Pest Control (Exterminator services)[cite: 1, 2]
                - 6701.6: Fire System Test & Monitor (Fire extinguisher inspections, alarm monitoring)[cite: 1, 2]
                - 6701.7: Equipment Rental[cite: 1, 2]
                - 6701.8: Music Service[cite: 1, 2]
                - 6701.9: Patrol Security[cite: 1, 2]
                - 6702.1: Contracted Repairs (Dryer repair, appliance repair, contractor repairs)[cite: 1, 2]
                - 6702.2: Furniture Repair[cite: 1, 2]
                - 6702.3: Painting/Decorating[cite: 1, 2]
                - 6702.4: Kitchen Equipment[cite: 1, 2]
                - 6702.5: Curtains & Drapes[cite: 1, 2]
                - 6702.6: Plumbing & HVAC supplies[cite: 1, 2]
                - 6702.7: Water Softener Supply & Repair[cite: 1, 2]
                - 6702.8: Building[cite: 1, 2]
                - 6702.9: Light Bulbs[cite: 1, 2]
                - 6703: Operating Supplies - Maint (Hardware, paint, filters, tools)[cite: 1, 2]
                - 6704: Van Gas, Oil & Repairs (Hotel shuttle gas, oil changes, shuttle maintenance)[cite: 1, 2]
                - 6750: Cable TV (Spectrum, DirecTV, Comcast cable bills)[cite: 1, 2]
                - 6801: Electricity (Electric utility bills)[cite: 1, 2]
                - 6802: Gas (Natural gas / Propane utility bills)[cite: 1, 2]
                - 6803: Water & Sewer utility (6803.1 House / 6803.2 Landscape / 6803.3 Sewer)[cite: 1, 2]

                --- 4. ADMIN, IT, SOFTWARE & GENERAL (6000 - 6050 series) ---
                - 6040.6: First Aid Kit (Cintas/First aid cabinet refills, employee safety supplies)[cite: 1, 2]
                - 6043: Postage/Fed Ex/Delivery (USPS, FedEx, UPS shipping)[cite: 1, 2]
                - 6044: Paper, Ink & Oper Supplies (Office paper, toner, printer cartridges, pens, keycards)[cite: 1, 2]
                - 6045.1: Equipment Repair & Replace (Office copiers, front desk printers)[cite: 1, 2]
                - 6045.2: Equipment Maintenance - Opera (Opera PMS support, hotel software maintenance)[cite: 1, 2]
                - 6045.3: Cellular Telephone (Staff mobile phones)[cite: 1, 2]
                - 6045.4: HP Computers (Hewlett Packard Financial Services, IT hardware leasing, server rentals)[cite: 1, 2]
                - 6045.5: Intuit Quickbooks Fees (Quickbooks subscriptions, accounting software fees)[cite: 1, 2]
                - 6050.1: Credit Card Commission (Merchant processing fees, interchange fees)[cite: 1, 2]
                - 6050.2: CLC Fees (Corporate Lodging Card fees)[cite: 1, 2]
                - 6050.3: CC Charge Backs/Write Downs[cite: 1, 2]

                --- 5. SALES, MARKETING, FRANCHISE & TAXES (6100 - 8000 series) ---
                - 6152: Dues/Subscriptions (Chamber of Commerce, hotel associations)[cite: 1, 2]
                - 6175.1: Outdoor Advertising (Billboards)[cite: 1, 2]
                - 6175.5: Print Media / Advertising[cite: 1, 2]
                - 6178: Weekly Guest Reception (Social hour food & beverage)[cite: 1, 2]
                - 6180: Travel Agent Fees & Commissions (Expedia, Booking.com commission fees)[cite: 1, 2]
                - 6601.1: Franchise Royalty Fee[cite: 1, 2]
                - 6601.2: Franchise Sales Fee[cite: 1, 2]
                - 6601.3: Brand Reservation Charges[cite: 1, 2]
                - 6601.4: Brand Training[cite: 1, 2]
                - 6602: Frequent Guest Plan (IHG Rewards, loyalty point fees)[cite: 1, 2]
                - 6902.1: Bank Charges (Returned check fees, wire fees, bank service charges)[cite: 1, 2]
                - 7000: Property Tax (County & City property tax bills)[cite: 1, 2]
                - 7002: License Permits & Fees (Health department permit, city operating licenses, elevator permits)[cite: 1, 2]
                - 7101: Property & Casualty (Property insurance premiums)[cite: 1, 2]
                - 7102: Liability (General liability insurance)[cite: 1, 2]
                - 7104: Worker's Compensation[cite: 1, 2]
                - 8001: Legal Fees (Attorney / legal services)[cite: 1, 2]
                - 8002: Audit, CPA, Tax Return Preparations (CPA tax preparation, accounting audit fees)[cite: 1, 2]

                Return strictly valid JSON matching this exact structure:
                {{
                    "vendor": "Vendor Name",
                    "invoice_number": "Invoice Number",
                    "invoice_date": "YYYY-MM-DD",
                    "invoice_total": 0.00,
                    "gl_summary": [
                        {{
                            "gl_code": "6045.4",
                            "gl_name": "HP Computers",
                            "subtotal": 0.00
                        }}
                    ],
                    "items": [
                        {{
                            "page_number": 1,
                            "item_code": "SKU",
                            "description": "Description",
                            "amount": 0.00,
                            "gl_code": "6045.4",
                            "gl_name": "HP Computers"
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
          parsed_data, used_channel = execute_with_key_rotation(contents)
          st.session_state["invoice_data"] = parsed_data
          st.toast(f"⚡ Successfully coded via {used_channel}!", icon="🚀")
        except Exception as e:
          st.error(f"Processing Error: {e}")

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

      with st.expander("🔍 View All Extracted Line Items", expanded=False):
        df_items = pd.DataFrame(data.get("items", []))
        st.dataframe(df_items, use_container_width=True, hide_index=True)

      d_col1, d_col2 = st.columns(2)
      if is_pdf:
        stamped_pdf_bytes = stamp_gl_summary_on_pdf(file_bytes, data)
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
