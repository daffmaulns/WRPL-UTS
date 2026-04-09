# 🎓 Alumni Dashboard System (Streamlit)

This project is a **Streamlit-based dashboard application** built to manage alumni event operations, including committee management, transaction auditing, and QR-based ticketing.

The system integrates directly with a relational database (`uts_dump.sql`) and provides an interactive interface for both administrators and participants.

---

# 🚀 Features

## 1. Internal Management — Board Period Management

This feature automates the management of committee/board member status based on their term period.

### Key Functionality

* Automatically detects expired board members using `end_year`
* Mass update: sets `is_active = 0` for expired terms
* Displays all board members (active & inactive)
* Visual status indicators:

  * 🟢 Active
  * ⚪ Demissioner

### How It Works

* Data is retrieved from `alumni_boards` joined with `alumni_users`
* A button triggers an SQL `UPDATE`:

  ```sql
  UPDATE alumni_boards
  SET is_active = 0
  WHERE end_year < 2026;
  ```
* The UI refreshes immediately after execution

---

## 2. Transaction Audit — Order Traceability

This feature allows administrators to track the full lifecycle of an order.

### Key Functionality

* Search or select `order_id`
* View complete order status history
* Timeline-style display (chronological)
* Event context included

### How It Works

* Uses JOIN across:

  * `merch_orders`
  * `merch_order_status_history`
  * `events`
* Results are sorted by timestamp
* Displayed as a readable audit trail

### Example Use Case

* Investigating vending machine failures
* Verifying order processing flow
* Handling user complaints

---

## 3. Tickets & Orders — QR Ticket System

This feature provides a unified QR-based system for:

* Event entry
* Merchandise pickup

### Key Functionality

* Dynamic QR code generation
* Ticket selection per participant
* Event-linked ticket display
* Scan simulation system

### How It Works

* QR data is retrieved from `event_participants`
* QR image is generated dynamically in the app
* Scan simulation:

  * User inputs QR code
  * System updates:

    ```sql
    attendance_status = 1
    ```
* UI refresh reflects attendance change

### Benefits

* One QR = Ticket + Pickup
* Faster check-in process
* Reduced manual validation

---

# 🏗️ Project Structure

```
project/
│
├── app.py                      # Main Streamlit application
├── alumni_dashboard_setup.ipynb # One-click setup notebook
├── uts_dump.sql                # Database schema & data
├── vouching.zip                # Supporting assets (images)
└── README.md                   # Documentation
```

---

# ⚙️ Setup & Installation

## 1. Install Dependencies

```bash
pip install streamlit pandas pillow qrcode
```

## 2. Initialize Database

Run the notebook:

```bash
alumni_dashboard_setup.ipynb
```

OR run the app directly (auto-initializes DB if needed)

## 3. Run the App

```bash
streamlit run app.py
```

---

# 🧠 Technical Overview

* **Frontend:** Streamlit
* **Database:** SQLite (converted from `.sql dump`)
* **Core Concepts:**

  * SQL JOIN operations
  * CRUD operations (Read + Update)
  * Dynamic UI rendering
  * QR code generation (PIL + qrcode)
  * State simulation (attendance update)

---

# 📌 Key Design Decisions

* Avoid hardcoded data → use selectable records
* Provide dropdowns for usability (instead of manual input)
* Immediate UI refresh after DB updates
* Separate logic:

  * Data layer (SQL)
  * UI layer (Streamlit)
  * Processing layer (Python functions)

---

# 💡 Future Improvements

* Pagination (10 / 25 / 50 rows)
* Filtering by event, year, or status
* Export audit logs (CSV / PDF)
* Real QR scanning integration (camera input)
* Authentication system for multi-user roles

---

# 🏁 Conclusion

This project demonstrates how a simple Streamlit application can be extended into a **functional operational dashboard** that supports:

* Internal organization management
* Transaction traceability
* Event participation systems

It combines **data visualization, database operations, and user interaction** into a cohesive system suitable for both academic and real-world use cases.
