from __future__ import annotations

import io
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Alumni Events Dashboard"
DB_FILENAME = "alumnievents.db"
SQL_CANDIDATES = ["uts_dump.sql"]
ZIP_CANDIDATES = ["vouching.zip", "voucher.zip"]
BOARD_SYNC_YEAR = 2026


# -----------------------------
# File / path helpers
# -----------------------------
def candidate_dirs() -> List[Path]:
    here = Path(__file__).resolve().parent
    dirs = [Path.cwd(), here, Path("/mnt/data")]
    unique = []
    seen = set()
    for d in dirs:
        key = str(d.resolve()) if d.exists() else str(d)
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def find_existing_file(candidates: Sequence[str]) -> Optional[Path]:
    for directory in candidate_dirs():
        for name in candidates:
            path = directory / name
            if path.exists():
                return path
    return None


def get_working_dir() -> Path:
    sql_path = find_existing_file(SQL_CANDIDATES)
    if sql_path:
        return sql_path.parent
    return Path(__file__).resolve().parent


# -----------------------------
# SQL dump parsing
# -----------------------------
def split_rows(values_block: str) -> List[str]:
    rows: List[str] = []
    depth = 0
    in_quote = False
    escape = False
    start = None

    for i, ch in enumerate(values_block):
        if in_quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                in_quote = False
        else:
            if ch == "'":
                in_quote = True
            elif ch == "(":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and start is not None:
                    rows.append(values_block[start : i + 1])
                    start = None
    return rows


def parse_sql_tuple(row_text: str) -> Tuple:
    inner = row_text[1:-1]
    values: List[str] = []
    current: List[str] = []
    in_quote = False
    escape = False

    for ch in inner:
        if in_quote:
            current.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "'":
                in_quote = False
        else:
            if ch == "'":
                in_quote = True
                current.append(ch)
            elif ch == ",":
                values.append("".join(current).strip())
                current = []
            else:
                current.append(ch)
    values.append("".join(current).strip())

    def convert(token: str):
        if token.upper() == "NULL":
            return None
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1].replace("\\'", "'").replace("\\\\", "\\")
        if re.fullmatch(r"-?\d+", token):
            return int(token)
        if re.fullmatch(r"-?\d+\.\d+", token):
            return float(token)
        return token

    return tuple(convert(v) for v in values)


def extract_insert_rows(sql_text: str, table_name: str) -> List[Tuple]:
    match = re.search(
        rf"INSERT INTO `{re.escape(table_name)}` VALUES (.*?);",
        sql_text,
        re.S,
    )
    if not match:
        raise ValueError(f"INSERT statement for `{table_name}` was not found.")
    rows = split_rows(match.group(1))
    return [parse_sql_tuple(row) for row in rows]


def build_database_from_sql(sql_path: Path, db_path: Path) -> None:
    sql_text = sql_path.read_text(encoding="utf-8")

    tables = [
        "alumni_users",
        "alumni_boards",
        "events",
        "event_participants",
        "merch_orders",
        "merch_order_status_history",
    ]

    schema = """
    PRAGMA foreign_keys = ON;

    CREATE TABLE alumni_users (
      user_id INTEGER PRIMARY KEY,
      full_name TEXT,
      email TEXT,
      role TEXT,
      member_type TEXT,
      member_id TEXT UNIQUE,
      graduation_year INTEGER,
      company TEXT
    );

    CREATE TABLE alumni_boards (
      board_id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL,
      position TEXT NOT NULL,
      start_year INTEGER NOT NULL,
      end_year INTEGER NOT NULL,
      is_active INTEGER DEFAULT 1,
      FOREIGN KEY (user_id) REFERENCES alumni_users(user_id)
    );

    CREATE TABLE events (
      event_id INTEGER PRIMARY KEY,
      event_name TEXT,
      description TEXT,
      start_date TEXT,
      max_quota INTEGER,
      ticket_price REAL,
      type_id INTEGER,
      location_id INTEGER
    );

    CREATE TABLE event_participants (
      participant_id INTEGER PRIMARY KEY,
      user_id INTEGER,
      event_id INTEGER,
      registration_date TEXT,
      qr_code TEXT,
      attendance_status INTEGER DEFAULT 0,
      FOREIGN KEY (user_id) REFERENCES alumni_users(user_id),
      FOREIGN KEY (event_id) REFERENCES events(event_id)
    );

    CREATE TABLE merch_orders (
      order_id INTEGER PRIMARY KEY,
      user_id INTEGER NOT NULL,
      event_id INTEGER,
      vending_machine_id TEXT,
      order_date TEXT,
      pickup_time TEXT,
      total_price REAL,
      order_status TEXT,
      FOREIGN KEY (user_id) REFERENCES alumni_users(user_id),
      FOREIGN KEY (event_id) REFERENCES events(event_id)
    );

    CREATE TABLE merch_order_status_history (
      history_id INTEGER PRIMARY KEY,
      order_id INTEGER NOT NULL,
      old_status TEXT,
      new_status TEXT NOT NULL,
      changed_at TEXT,
      notes TEXT,
      FOREIGN KEY (order_id) REFERENCES merch_orders(order_id) ON DELETE CASCADE
    );

    CREATE INDEX idx_boards_user ON alumni_boards(user_id);
    CREATE INDEX idx_boards_end_year ON alumni_boards(end_year);
    CREATE INDEX idx_events_start_date ON events(start_date);
    CREATE INDEX idx_participants_user_event ON event_participants(user_id, event_id);
    CREATE INDEX idx_participants_qr_code ON event_participants(qr_code);
    CREATE INDEX idx_orders_user_event ON merch_orders(user_id, event_id);
    CREATE INDEX idx_orders_order_date ON merch_orders(order_date);
    CREATE INDEX idx_status_history_order_changed ON merch_order_status_history(order_id, changed_at);
    """

    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema)
        for table in tables:
            rows = extract_insert_rows(sql_text, table)
            placeholders = ",".join(["?"] * len(rows[0]))
            conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.commit()


def extract_vouching_archive(zip_path: Optional[Path], extract_dir: Path) -> None:
    if not zip_path or not zip_path.exists():
        return
    extract_dir.mkdir(parents=True, exist_ok=True)
    if any(extract_dir.iterdir()):
        return
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)


def ensure_environment() -> Tuple[Path, Optional[Path], Optional[Path]]:
    working_dir = get_working_dir()
    db_path = working_dir / DB_FILENAME
    sql_path = find_existing_file(SQL_CANDIDATES)
    zip_path = find_existing_file(ZIP_CANDIDATES)
    voucher_dir = working_dir / "vouching"

    if not db_path.exists():
        if not sql_path:
            raise FileNotFoundError(
                "uts_dump.sql was not found. Put app.py in the same folder as uts_dump.sql."
            )
        build_database_from_sql(sql_path, db_path)

    extract_vouching_archive(zip_path, voucher_dir)
    return db_path, sql_path, voucher_dir if voucher_dir.exists() else None


# -----------------------------
# DB utilities
# -----------------------------
def query_df(db_path: Path, query: str, params: Sequence = ()) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        return pd.read_sql_query(query, conn, params=params)


def execute_write(db_path: Path, query: str, params: Sequence = ()) -> int:
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(query, params)
        conn.commit()
        return cur.rowcount


def execute_many(db_path: Path, script: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(script)
        conn.commit()


# -----------------------------
# Formatters / UI helpers
# -----------------------------
def format_currency(value) -> str:
    if pd.isna(value):
        return "-"
    return f"Rp {float(value):,.0f}".replace(",", ".")


def status_badge(active_value: int) -> str:
    return "🟢 Aktif" if int(active_value) == 1 else "⚪ Demisioner"


def attendance_badge(value: int) -> str:
    return "✅ Hadir" if int(value) == 1 else "⏳ Belum Hadir"


def proof_path(voucher_dir: Optional[Path], order_id: int) -> Optional[Path]:
    if voucher_dir is None:
        return None
    path = voucher_dir / "vouching" / f"proof_{order_id}.jpg"
    if path.exists():
        return path
    path = voucher_dir / f"proof_{order_id}.jpg"
    return path if path.exists() else None


def build_qr_image(qr_text: str):
    import io

    try:
        import qrcode

        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(qr_text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        # Streamlit on newer Python versions can be picky with qrcode's wrapper type,
        # so convert the image to raw PNG bytes before passing it to st.image.
        if hasattr(img, "get_image"):
            img = img.get_image()
        elif hasattr(img, "convert"):
            img = img.convert("RGB")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 20, 220, 220), outline="black", width=3)
        draw.text((30, 110), qr_text, fill="black")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()


def dataframe_pager(df: pd.DataFrame, page_size: int, page_key: str) -> pd.DataFrame:
    total_rows = len(df)
    if total_rows == 0:
        return df
    total_pages = max(1, (total_rows - 1) // page_size + 1)
    page = st.number_input(
        "Halaman",
        min_value=1,
        max_value=total_pages,
        value=1,
        step=1,
        key=page_key,
    )
    start = (page - 1) * page_size
    end = start + page_size
    st.caption(f"Menampilkan baris {start + 1}-{min(end, total_rows)} dari {total_rows}")
    return df.iloc[start:end].copy()


# -----------------------------
# Page: Manajemen Internal
# -----------------------------
def render_board_page(db_path: Path) -> None:
    st.header("Manajemen Internal")
    st.caption("Board Period Management — sinkronisasi status pengurus lama dan tampilkan daftar pengurus dengan label visual.")

    col_a, col_b = st.columns([1, 2])
    with col_a:
        if st.button(f"Sinkronkan status demisioner (< {BOARD_SYNC_YEAR})", use_container_width=True):
            updated = execute_write(
                db_path,
                """
                UPDATE alumni_boards
                SET is_active = 0
                WHERE end_year < ? AND COALESCE(is_active, 1) <> 0
                """,
                (BOARD_SYNC_YEAR,),
            )
            st.success(f"{updated} record pengurus diperbarui menjadi demisioner.")
    with col_b:
        st.info(
            f"Logika ujian dijalankan sesuai instruksi: semua pengurus dengan end_year < {BOARD_SYNC_YEAR} akan di-set is_active = 0."
        )

    boards = query_df(
        db_path,
        """
        SELECT
            ab.board_id,
            au.full_name,
            au.email,
            ab.position,
            ab.start_year,
            ab.end_year,
            ab.is_active
        FROM alumni_boards ab
        JOIN alumni_users au ON au.user_id = ab.user_id
        ORDER BY ab.end_year DESC, ab.start_year DESC, ab.position ASC
        """,
    )
    boards["status_visual"] = boards["is_active"].fillna(0).astype(int).apply(status_badge)

    c1, c2, c3 = st.columns([1, 1, 1])
    with c1:
        status_filter = st.selectbox("Filter status", ["Semua", "Aktif", "Demisioner"])
    with c2:
        position_filter = st.multiselect("Filter posisi", sorted(boards["position"].dropna().unique().tolist()))
    with c3:
        page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=2)

    filtered = boards.copy()
    if status_filter == "Aktif":
        filtered = filtered[filtered["is_active"] == 1]
    elif status_filter == "Demisioner":
        filtered = filtered[filtered["is_active"] == 0]

    if position_filter:
        filtered = filtered[filtered["position"].isin(position_filter)]

    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("Total Pengurus", len(boards))
    metric_2.metric("Aktif", int((boards["is_active"] == 1).sum()))
    metric_3.metric("Demisioner", int((boards["is_active"] == 0).sum()))

    display_df = filtered[
        ["board_id", "full_name", "position", "start_year", "end_year", "status_visual", "email"]
    ].rename(
        columns={
            "board_id": "Board ID",
            "full_name": "Nama",
            "position": "Jabatan",
            "start_year": "Mulai",
            "end_year": "Selesai",
            "status_visual": "Status",
            "email": "Email",
        }
    )

    display_df = dataframe_pager(display_df, page_size, "boards_page")
    st.dataframe(display_df, use_container_width=True, hide_index=True)


# -----------------------------
# Page: Audit Transaksi
# -----------------------------
def render_timeline(history_df: pd.DataFrame) -> None:
    for i, row in history_df.reset_index(drop=True).iterrows():
        old_status = row["old_status"] if pd.notna(row["old_status"]) else "-"
        notes = row["notes"] if pd.notna(row["notes"]) else "-"
        with st.container(border=True):
            st.markdown(f"**{i + 1}. {old_status} → {row['new_status']}**")
            st.caption(str(row["changed_at"]))
            st.write(notes)


def render_audit_page(db_path: Path, voucher_dir: Optional[Path]) -> None:
    st.header("Audit Transaksi")
    st.caption("Order Traceability Audit — pilih order, lihat histori status kronologis, dan cek bukti vouching jika tersedia.")

    base_orders = query_df(
        db_path,
        """
        SELECT
            mo.order_id,
            mo.order_date,
            mo.pickup_time,
            mo.total_price,
            mo.order_status,
            mo.vending_machine_id,
            au.full_name,
            e.event_name
        FROM merch_orders mo
        JOIN alumni_users au ON au.user_id = mo.user_id
        LEFT JOIN events e ON e.event_id = mo.event_id
        ORDER BY mo.order_date DESC, mo.order_id DESC
        """
    )

    filter_col_1, filter_col_2, filter_col_3 = st.columns([1.2, 1, 1])
    with filter_col_1:
        search_text = st.text_input("Cari order / user / event", "")
    with filter_col_2:
        status_filter = st.selectbox(
            "Filter status order",
            ["Semua"] + sorted(base_orders["order_status"].dropna().unique().tolist()),
        )
    with filter_col_3:
        page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=2)

    orders = base_orders.copy()
    if search_text:
        needle = search_text.strip().lower()
        orders = orders[
            orders["order_id"].astype(str).str.contains(needle, case=False, regex=False)
            | orders["full_name"].str.lower().str.contains(needle, regex=False)
            | orders["event_name"].fillna("").str.lower().str.contains(needle, regex=False)
        ]
    if status_filter != "Semua":
        orders = orders[orders["order_status"] == status_filter]

    orders["proof_available"] = orders["order_id"].apply(lambda x: "Ya" if proof_path(voucher_dir, int(x)) else "Tidak")

    st.subheader("Daftar order terbaru")
    preview_df = orders.rename(
        columns={
            "order_id": "Order ID",
            "order_date": "Order Date",
            "pickup_time": "Pickup Time",
            "total_price": "Total Price",
            "order_status": "Status",
            "vending_machine_id": "VM",
            "full_name": "Nama",
            "event_name": "Event",
            "proof_available": "Voucher Proof",
        }
    )
    preview_df["Total Price"] = preview_df["Total Price"].apply(format_currency)
    preview_df = dataframe_pager(preview_df, page_size, "orders_page")
    st.dataframe(preview_df, use_container_width=True, hide_index=True)

    candidate_orders = orders["order_id"].astype(int).tolist()
    if not candidate_orders:
        st.warning("Tidak ada order yang cocok dengan filter.")
        return

    st.subheader("Pilih order untuk audit")
    default_order = candidate_orders[0]
    label_map = {
        int(row.order_id): f"{int(row.order_id)} | {row.full_name} | {row.event_name} | {row.order_status}"
        for row in orders.itertuples(index=False)
    }

    selected_from_list = st.selectbox(
        "Pilih dari daftar",
        options=candidate_orders[: min(len(candidate_orders), 500)],
        format_func=lambda x: label_map.get(int(x), str(x)),
    )

    with st.form("manual_order_form"):
        manual_order = st.number_input(
            "Atau input order_id manual",
            min_value=0,
            value=int(selected_from_list),
            step=1,
        )
        submitted = st.form_submit_button("Tampilkan histori")

    target_order_id = int(manual_order if submitted else selected_from_list)

    summary = query_df(
        db_path,
        """
        SELECT
            mo.order_id,
            mo.order_date,
            mo.pickup_time,
            mo.total_price,
            mo.order_status,
            mo.vending_machine_id,
            au.full_name,
            au.email,
            e.event_name,
            e.start_date
        FROM merch_orders mo
        JOIN alumni_users au ON au.user_id = mo.user_id
        LEFT JOIN events e ON e.event_id = mo.event_id
        WHERE mo.order_id = ?
        """,
        (target_order_id,),
    )

    if summary.empty:
        st.error(f"Order ID {target_order_id} tidak ditemukan.")
        return

    row = summary.iloc[0]
    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Order ID", str(int(row["order_id"])))
    metric_2.metric("Status Akhir", row["order_status"])
    metric_3.metric("Total Harga", format_currency(row["total_price"]))
    metric_4.metric("Vending Machine", row["vending_machine_id"] if pd.notna(row["vending_machine_id"]) else "-")

    st.write(
        {
            "Nama": row["full_name"],
            "Email": row["email"],
            "Event": row["event_name"],
            "Tanggal Event": row["start_date"],
            "Order Date": row["order_date"],
            "Pickup Time": row["pickup_time"],
        }
    )

    history = query_df(
        db_path,
        """
        SELECT
            h.history_id,
            h.old_status,
            h.new_status,
            h.changed_at,
            h.notes
        FROM merch_order_status_history h
        WHERE h.order_id = ?
        ORDER BY h.changed_at ASC, h.history_id ASC
        """,
        (target_order_id,),
    )

    st.subheader("Timeline histori status")
    if history.empty:
        st.warning("Belum ada histori status untuk order ini.")
    else:
        render_timeline(history)

    image_path = proof_path(voucher_dir, target_order_id)
    st.subheader("Bukti vouching")
    if image_path:
        st.image(str(image_path), caption=image_path.name, width=360)
    else:
        st.info("Tidak ada file proof_*.jpg untuk order ini di arsip vouching.")


# -----------------------------
# Page: Tiket & Order
# -----------------------------
def render_ticket_mockup() -> None:
    st.markdown(
        """
        <div style="
            max-width: 420px;
            margin: 0 auto 1rem auto;
            border-radius: 24px;
            padding: 20px;
            background: linear-gradient(135deg, #0f172a, #1e3a8a);
            color: white;
            box-shadow: 0 10px 30px rgba(0,0,0,0.18);
        ">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    <div style="font-size:12px; opacity:0.8;">Tiket & Pickup</div>
                    <div style="font-size:22px; font-weight:700;">Unified QR Pass</div>
                </div>
                <div style="
                    background: rgba(255,255,255,0.14);
                    padding: 6px 10px;
                    border-radius: 999px;
                    font-size: 12px;
                ">Mockup UI</div>
            </div>

            <div style="
                margin-top:18px;
                background:white;
                color:#111827;
                border-radius:20px;
                padding:18px;
            ">
                <div style="font-weight:700; font-size:18px;">Deep Learning Programming Training</div>
                <div style="font-size:13px; color:#6b7280;">Alumni: Ahmad Sanjaya Sasmita</div>

                <div style="
                    margin: 18px auto;
                    width: 180px;
                    height: 180px;
                    border-radius: 16px;
                    background:
                        linear-gradient(90deg, #111 10px, transparent 10px) 0 0/30px 30px,
                        linear-gradient(#111 10px, transparent 10px) 0 0/30px 30px,
                        #fff;
                    border: 10px solid #111827;
                "></div>

                <div style="display:flex; gap:10px; flex-wrap:wrap; font-size:13px;">
                    <div style="background:#eef2ff; color:#3730a3; padding:8px 12px; border-radius:999px;">QR Masuk Acara</div>
                    <div style="background:#ecfeff; color:#155e75; padding:8px 12px; border-radius:999px;">QR Pickup Barang</div>
                    <div style="background:#f3f4f6; color:#374151; padding:8px 12px; border-radius:999px;">Attendance Sync</div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_ticket_page(db_path: Path) -> None:
    st.header("Tiket & Order")

    tab1, tab2, tab3 = st.tabs(["Mockup UI/UX", "Tiket Saya", "Scan Simulator"])

    with tab1:
        st.caption("Desain mockup untuk fitur self-service QR terpadu.")
        render_ticket_mockup()

    participants = query_df(
        db_path,
        """
        SELECT
            ep.participant_id,
            ep.qr_code,
            ep.attendance_status,
            ep.registration_date,
            au.user_id,
            au.full_name,
            au.email,
            e.event_id,
            e.event_name,
            e.start_date
        FROM event_participants ep
        JOIN alumni_users au ON au.user_id = ep.user_id
        JOIN events e ON e.event_id = ep.event_id
        ORDER BY ep.registration_date DESC, ep.participant_id DESC
        """
    )

    with tab2:
        st.caption("Pilih tiket berdasarkan alumni atau event, lalu tampilkan QR dinamis dan order terkait.")
        col1, col2, col3 = st.columns([1.2, 1, 1])
        with col1:
            search_text = st.text_input("Cari alumni / event", key="ticket_search")
        with col2:
            event_filter = st.selectbox(
                "Filter event",
                ["Semua"] + sorted(participants["event_name"].dropna().unique().tolist()),
                key="ticket_event_filter",
            )
        with col3:
            page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=1, key="ticket_page_size")

        filtered = participants.copy()
        if search_text:
            needle = search_text.strip().lower()
            filtered = filtered[
                filtered["full_name"].str.lower().str.contains(needle, regex=False)
                | filtered["event_name"].str.lower().str.contains(needle, regex=False)
                | filtered["qr_code"].str.lower().str.contains(needle, regex=False)
            ]
        if event_filter != "Semua":
            filtered = filtered[filtered["event_name"] == event_filter]

        table_df = filtered[
            ["participant_id", "full_name", "event_name", "registration_date", "attendance_status", "qr_code"]
        ].rename(
            columns={
                "participant_id": "Participant ID",
                "full_name": "Nama",
                "event_name": "Event",
                "registration_date": "Registration Date",
                "attendance_status": "Attendance",
                "qr_code": "QR Code",
            }
        )
        table_df["Attendance"] = table_df["Attendance"].apply(attendance_badge)
        table_df = dataframe_pager(table_df, page_size, "participants_page")
        st.dataframe(table_df, use_container_width=True, hide_index=True)

        if filtered.empty:
            st.warning("Tidak ada tiket yang cocok dengan filter.")
        else:
            ids = filtered["participant_id"].astype(int).tolist()
            label_map = {
                int(r.participant_id): f"{int(r.participant_id)} | {r.full_name} | {r.event_name}"
                for r in filtered.itertuples(index=False)
            }
            selected_participant_id = st.selectbox(
                "Pilih participant record",
                options=ids[: min(len(ids), 500)],
                format_func=lambda x: label_map.get(int(x), str(x)),
            )

            chosen = filtered[filtered["participant_id"] == selected_participant_id].iloc[0]
            metric_1, metric_2, metric_3 = st.columns(3)
            metric_1.metric("Alumni", chosen["full_name"])
            metric_2.metric("Event", chosen["event_name"])
            metric_3.metric("Attendance", attendance_badge(int(chosen["attendance_status"])))

            qr_img = build_qr_image(str(chosen["qr_code"]))
            qr_col, info_col = st.columns([1, 1.3])
            with qr_col:
                st.image(qr_img, caption=str(chosen["qr_code"]), width=260)
            with info_col:
                st.write(
                    {
                        "Participant ID": int(chosen["participant_id"]),
                        "User ID": int(chosen["user_id"]),
                        "Email": chosen["email"],
                        "Tanggal Event": chosen["start_date"],
                        "Tanggal Registrasi": chosen["registration_date"],
                    }
                )

            related_orders = query_df(
                db_path,
                """
                SELECT
                    order_id,
                    order_date,
                    pickup_time,
                    total_price,
                    order_status,
                    vending_machine_id
                FROM merch_orders
                WHERE user_id = ? AND event_id = ?
                ORDER BY order_date DESC, order_id DESC
                """,
                (int(chosen["user_id"]), int(chosen["event_id"])),
            )
            st.subheader("Order terkait pada event yang sama")
            if related_orders.empty:
                st.info("Tidak ada merch order untuk alumni dan event ini.")
            else:
                related_orders["total_price"] = related_orders["total_price"].apply(format_currency)
                st.dataframe(related_orders, use_container_width=True, hide_index=True)

    with tab3:
        st.caption("Simulasi scan QR: jika kode valid di-input, attendance_status akan diubah menjadi 1 (Hadir).")
        qr_candidates = participants["qr_code"].dropna().astype(str).tolist()
        default_qr = qr_candidates[0] if qr_candidates else ""

        scan_option = st.selectbox(
            "Pilih QR dari data",
            options=qr_candidates[: min(len(qr_candidates), 500)] if qr_candidates else [""],
            index=0,
        )
        input_qr = st.text_input("Atau input QR secara manual", value=scan_option)

        if st.button("Scan & Update Attendance", use_container_width=True):
            updated = execute_write(
                db_path,
                """
                UPDATE event_participants
                SET attendance_status = 1
                WHERE qr_code = ?
                """,
                (input_qr.strip(),),
            )

            if updated > 0:
                st.success(f"QR valid. {updated} record attendance berhasil diubah menjadi Hadir.")
            else:
                st.error("QR tidak ditemukan.")

        if input_qr.strip():
            scan_result = query_df(
                db_path,
                """
                SELECT
                    ep.participant_id,
                    ep.qr_code,
                    ep.attendance_status,
                    au.full_name,
                    e.event_name,
                    e.start_date
                FROM event_participants ep
                JOIN alumni_users au ON au.user_id = ep.user_id
                JOIN events e ON e.event_id = ep.event_id
                WHERE ep.qr_code = ?
                """,
                (input_qr.strip(),),
            )
            if not scan_result.empty:
                st.dataframe(
                    scan_result.assign(
                        attendance_status=scan_result["attendance_status"].apply(attendance_badge)
                    ),
                    use_container_width=True,
                    hide_index=True,
                )


# -----------------------------
# Main app
# -----------------------------
def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🎟️", layout="wide")
    st.title(APP_TITLE)

    try:
        db_path, sql_path, voucher_dir = ensure_environment()
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    with st.sidebar:
        st.header("Menu")
        page = st.radio(
            "Pilih fitur",
            ["Manajemen Internal", "Audit Transaksi", "Tiket & Order"],
        )
        st.divider()
        st.caption(f"Database: {db_path.name}")
        if sql_path:
            st.caption(f"SQL source: {sql_path.name}")
        st.caption("Auto-init aktif: database akan dibuat otomatis jika belum ada.")

    if page == "Manajemen Internal":
        render_board_page(db_path)
    elif page == "Audit Transaksi":
        render_audit_page(db_path, voucher_dir)
    else:
        render_ticket_page(db_path)


if __name__ == "__main__":
    main()
