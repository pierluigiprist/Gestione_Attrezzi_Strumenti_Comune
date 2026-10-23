"""
REGISTRO ATTREZZI - tracciamento attrezzi del magazzino con QR code.

Installazione (una volta sola):
    pip install streamlit pandas "qrcode[pil]"

Avvio (sul PC/server che resta acceso):
    streamlit run registro_attrezzi.py --server.address 0.0.0.0

I tecnici aprono dal telefono (stessa rete Wi-Fi):  http://IP-DEL-PC:8501

Al primo avvio il programma crea da solo, nella stessa cartella di questo file,
la cartella "dati_attrezzi" con il database e il file movimenti.csv
(aggiornato automaticamente a ogni movimento).
"""
import io
import re
import sqlite3
import urllib.parse
import zipfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

try:
    import qrcode
except ImportError:
    qrcode = None

ADMIN_PIN = "1234"  # <-- CAMBIALO: serve per la scheda Gestione

# Cartella dati creata automaticamente accanto a questo file
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "dati_attrezzi"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "attrezzi.db"
CSV_PATH = DATA_DIR / "movimenti.csv"


# ---------------------------------------------------------------- database
@contextmanager
def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS attrezzi(
            codice TEXT PRIMARY KEY, nome TEXT NOT NULL, categoria TEXT DEFAULT '',
            stato TEXT DEFAULT 'disponibile', tecnico TEXT, dal TEXT);
        CREATE TABLE IF NOT EXISTS tecnici(nome TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS movimenti(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, codice TEXT,
            attrezzo TEXT, tipo TEXT, tecnico TEXT);
        CREATE TABLE IF NOT EXISTS config(chiave TEXT PRIMARY KEY, valore TEXT);
        """)


def get_cfg(chiave, default=""):
    with db() as con:
        r = con.execute("SELECT valore FROM config WHERE chiave=?", (chiave,)).fetchone()
    return r["valore"] if r else default


def set_cfg(chiave, valore):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO config VALUES(?,?)", (chiave, valore))


def lista_attrezzi():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM attrezzi ORDER BY nome COLLATE NOCASE")]


def lista_tecnici():
    with db() as con:
        return [r["nome"] for r in con.execute("SELECT nome FROM tecnici ORDER BY nome COLLATE NOCASE")]


def movimenti_df():
    with db() as con:
        df = pd.read_sql_query(
            "SELECT ts, codice, attrezzo, tipo, tecnico FROM movimenti ORDER BY id DESC", con)
    df["tipo"] = df["tipo"].map({"prelievo": "Preso in carico", "rientro": "Rimesso a posto"})
    df.columns = ["Data e ora", "Codice", "Attrezzo", "Movimento", "Tecnico"]
    return df


def csv_bytes():
    return movimenti_df().to_csv(sep=";", index=False).encode("utf-8-sig")


def salva_csv():
    CSV_PATH.write_bytes(csv_bytes())


def muovi(codice, tecnico, tipo):
    ora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db() as con:
        a = con.execute("SELECT * FROM attrezzi WHERE codice=?", (codice,)).fetchone()
        if not a:
            return False, "Attrezzo non trovato."
        if tipo == "prelievo":
            cur = con.execute(
                "UPDATE attrezzi SET stato='in_uso', tecnico=?, dal=? WHERE codice=? AND stato!='in_uso'",
                (tecnico, ora, codice))
            if cur.rowcount == 0:
                return False, "Appena preso da " + str(a["tecnico"]) + "."
        else:
            cur = con.execute(
                "UPDATE attrezzi SET stato='disponibile', tecnico=NULL, dal=? WHERE codice=? AND stato='in_uso'",
                (ora, codice))
            if cur.rowcount == 0:
                return False, "Risulta già disponibile."
        con.execute(
            "INSERT INTO movimenti(ts,codice,attrezzo,tipo,tecnico) VALUES(?,?,?,?,?)",
            (ora, codice, a["nome"], tipo, tecnico))
    salva_csv()
    verbo = "preso in carico" if tipo == "prelievo" else "rimesso a posto"
    return True, a["nome"] + ": " + verbo + "."


# ---------------------------------------------------------------- utilità
def fmt(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d/%m %H:%M")
    except (TypeError, ValueError):
        return "-"


def pulisci_codice(c):
    return re.sub(r"[^A-Z0-9_.-]", "-", c.strip().upper())


def url_attrezzo(codice):
    base = get_cfg("base_url", "http://localhost:8501").rstrip("/")
    return base + "/?a=" + urllib.parse.quote(codice)


def qr_png(testo):
    buf = io.BytesIO()
    qrcode.make(testo).save(buf, format="PNG")
    return buf.getvalue()


def flash(msg, ok=True):
    st.session_state["flash"] = (msg, ok)


def scheda(a, tecnico, ctx):
    if a["stato"] == "in_uso":
        st.warning("In uso: **" + str(a["tecnico"]) + "** dal " + fmt(a["dal"]))
    else:
        st.success("Disponibile")
    if not tecnico:
        st.info("Scegli il tuo nome in alto per registrare il movimento.")
        return
    if a["stato"] == "in_uso":
        mio = a["tecnico"] == tecnico
        label = "✅ Rimesso a posto" if mio else "✅ Segna rimesso a posto (preso da " + str(a["tecnico"]) + ")"
        if st.button(label, key=ctx + "_r_" + a["codice"], type="primary", use_container_width=True):
            ok, msg = muovi(a["codice"], tecnico, "rientro")
            flash(msg, ok)
            st.query_params.pop("a", None)
            st.rerun()
    else:
        if st.button("🔧 Preso in carico", key=ctx + "_p_" + a["codice"], type="primary", use_container_width=True):
            ok, msg = muovi(a["codice"], tecnico, "prelievo")
            flash(msg, ok)
            st.query_params.pop("a", None)
            st.rerun()


# ---------------------------------------------------------------- interfaccia
st.set_page_config(page_title="Registro Attrezzi", page_icon="🔧", layout="centered")
init_db()
st.title("🔧 Registro Attrezzi")

if "flash" in st.session_state:
    msg, ok = st.session_state.pop("flash")
    (st.success if ok else st.error)(msg)

tecnici = lista_tecnici()
attrezzi = lista_attrezzi()
preset = st.query_params.get("t")
opzioni = ["- scegli il tuo nome -"] + tecnici
idx = opzioni.index(preset) if preset in tecnici else 0
scelta = st.selectbox("Chi sei?", opzioni, index=idx)
tecnico = scelta if scelta in tecnici else None
if not tecnici:
    st.info("Nessun tecnico inserito: aggiungili dalla scheda Gestione.")

# Arrivo da QR code: mostra subito la scheda dell'attrezzo
codice_qr = st.query_params.get("a")
if codice_qr:
    trovato = next((x for x in attrezzi if x["codice"] == codice_qr), None)
    with st.container(border=True):
        if trovato:
            st.subheader(trovato["nome"])
            st.caption(trovato["codice"] + (" · " + trovato["categoria"] if trovato["categoria"] else ""))
            scheda(trovato, tecnico, "qr")
        else:
            st.error("Attrezzo " + codice_qr + " non trovato.")
        if st.button("Chiudi scheda", key="chiudi_qr"):
            st.query_params.pop("a", None)
            st.rerun()

tab1, tab2, tab3 = st.tabs(["Attrezzi", "Movimenti", "Gestione"])

with tab1:
    in_uso = sum(1 for x in attrezzi if x["stato"] == "in_uso")
    c1, c2 = st.columns(2)
    c1.metric("Disponibili", len(attrezzi) - in_uso)
    c2.metric("In uso", in_uso)
    cerca = st.text_input("Cerca attrezzo o codice", "").lower()
    filtro = st.radio("Mostra", ["Tutti", "Disponibili", "In uso", "Miei"], horizontal=True)
    for a in attrezzi:
        if cerca and cerca not in (a["nome"] + " " + a["codice"] + " " + (a["categoria"] or "")).lower():
            continue
        if filtro == "Disponibili" and a["stato"] == "in_uso":
            continue
        if filtro == "In uso" and a["stato"] != "in_uso":
            continue
        if filtro == "Miei" and not (a["stato"] == "in_uso" and a["tecnico"] == tecnico):
            continue
        stato = "🟠 " + str(a["tecnico"]) + " (" + fmt(a["dal"]) + ")" if a["stato"] == "in_uso" else "🟢 Disponibile"
        with st.expander(a["nome"] + " · " + a["codice"] + "  —  " + stato):
            scheda(a, tecnico, "lst")
    if not attrezzi:
        st.caption("Nessun attrezzo inserito.")

with tab2:
    df = movimenti_df()
    st.download_button("⬇ Esporta movimenti in CSV", csv_bytes(),
                       "movimenti_attrezzi_" + datetime.now().strftime("%Y-%m-%d") + ".csv",
                       "text/csv", use_container_width=True)
    st.caption("Una copia aggiornata è salvata anche in: " + str(CSV_PATH))
    st.dataframe(df, use_container_width=True, hide_index=True)

with tab3:
    pin = st.text_input("PIN gestione", type="password")
    if pin != ADMIN_PIN:
        st.info("Inserisci il PIN per gestire attrezzi, tecnici ed etichette.")
    else:
        st.subheader("Aggiungi attrezzo")
        with st.form("nuovo_attrezzo", clear_on_submit=True):
            c = st.text_input("Codice (es. TR-001)")
            n = st.text_input("Nome (es. Trapano Bosch)")
            k = st.text_input("Categoria (facoltativa)")
            if st.form_submit_button("Aggiungi"):
                c = pulisci_codice(c)
                if not c or not n.strip():
                    st.error("Servono codice e nome.")
                else:
                    try:
                        with db() as con:
                            con.execute("INSERT INTO attrezzi(codice,nome,categoria) VALUES(?,?,?)",
                                        (c, n.strip(), k.strip()))
                        flash("Attrezzo " + c + " aggiunto.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Codice già esistente.")

        st.subheader("Elimina attrezzo")
        if attrezzi:
            da_eliminare = st.selectbox("Attrezzo", [x["codice"] + " - " + x["nome"] for x in attrezzi])
            conferma = st.checkbox("Confermo l’eliminazione (lo storico dei movimenti resta)")
            if st.button("Elimina") and conferma:
                with db() as con:
                    con.execute("DELETE FROM attrezzi WHERE codice=?", (da_eliminare.split(" - ")[0],))
                flash("Attrezzo eliminato.")
                st.rerun()

        st.subheader("Tecnici")
        with st.form("nuovo_tecnico", clear_on_submit=True):
            t = st.text_input("Nome e cognome del tecnico")
            if st.form_submit_button("Aggiungi tecnico") and t.strip():
                with db() as con:
                    con.execute("INSERT OR IGNORE INTO tecnici VALUES(?)", (t.strip(),))
                st.rerun()
        if tecnici:
            rim = st.selectbox("Rimuovi tecnico", tecnici)
            if st.button("Rimuovi"):
                with db() as con:
                    con.execute("DELETE FROM tecnici WHERE nome=?", (rim,))
                st.rerun()

        st.subheader("Etichette QR")
        st.caption("Indirizzo con cui i telefoni raggiungono l’app, es. http://192.168.1.20:8501")
        base = st.text_input("Indirizzo dell’app", get_cfg("base_url", "http://localhost:8501"))
        if st.button("Salva indirizzo"):
            set_cfg("base_url", base.strip())
            st.rerun()
        if qrcode is None:
            st.warning('Per generare i QR installa: pip install "qrcode[pil]"')
        elif attrezzi:
            zbuf = io.BytesIO()
            cols = st.columns(3)
            with zipfile.ZipFile(zbuf, "w") as z:
                for i, a in enumerate(attrezzi):
                    png = qr_png(url_attrezzo(a["codice"]))
                    z.writestr(a["codice"] + ".png", png)
                    cols[i % 3].image(png, caption=a["codice"] + " · " + a["nome"], width=150)
            st.download_button("⬇ Scarica tutti i QR (ZIP)", zbuf.getvalue(), "etichette_qr.zip", "application/zip")
