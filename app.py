import streamlit as st
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from datetime import datetime
import re
import time

def ottieni_mappa_aule():
    url = "https://www.unipa.it/scuole/dimedicinaechirurgia/struttura/luoghi.html"
    mappa = {}
    try:
        response = requests.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        links = soup.find_all('a', href=re.compile(r'calendar\.seam\?oidAula=(\d+)'))
        for link in links:
            href = link.get('href')
            nome_aula = link.text.strip().lower()
            match = re.search(r'oidAula=(\d+)', href)
            if match and nome_aula:
                oid = int(match.group(1))
                nome_aula = nome_aula.replace("calendario", "").strip()
                mappa[oid] = nome_aula
        return mappa
    except Exception as e:
        st.error(f"Errore nel recupero lista aule: {e}")
        return {}

def calcola_spazi_liberi_filtrati(orari_occupati, durata_minima_minuti):
    inizio_giornata = datetime.strptime("08:00", "%H:%M")
    fine_giornata = datetime.strptime("19:00", "%H:%M")
    spazi_validi = []

    if not orari_occupati:
        durata_totale = (fine_giornata - inizio_giornata).total_seconds() / 60
        if durata_totale >= durata_minima_minuti:
            return ["8:00-19:00"]
        return []

    intervalli = []
    for inizio_str, fine_str in orari_occupati:
        inizio = datetime.strptime(inizio_str, "%H:%M")
        fine = datetime.strptime(fine_str, "%H:%M")
        intervalli.append((inizio, fine))
    
    intervalli.sort(key=lambda x: x[0])
    intervalli_uniti = [intervalli[0]]
    
    for corrente in intervalli[1:]:
        ultimo = intervalli_uniti[-1]
        if corrente[0] <= ultimo[1]:
            nuova_fine = max(ultimo[1], corrente[1])
            intervalli_uniti[-1] = (ultimo[0], nuova_fine)
        else:
            intervalli_uniti.append(corrente)

    orario_attuale = inizio_giornata
    for lezione_inizio, lezione_fine in intervalli_uniti:
        if orario_attuale < lezione_inizio:
            durata_buco = (lezione_inizio - orario_attuale).total_seconds() / 60
            if durata_buco >= durata_minima_minuti:
                ora_in = orario_attuale.strftime('%H:%M').lstrip("0").replace("00:", "0:")
                ora_fin = lezione_inizio.strftime('%H:%M').lstrip("0").replace("00:", "0:")
                spazi_validi.append(f"{ora_in}-{ora_fin}")
        orario_attuale = max(orario_attuale, lezione_fine)

    if orario_attuale < fine_giornata:
        durata_buco = (fine_giornata - orario_attuale).total_seconds() / 60
        if durata_buco >= durata_minima_minuti:
            ora_in = orario_attuale.strftime('%H:%M').lstrip("0").replace("00:", "0:")
            ora_fin = fine_giornata.strftime('%H:%M').lstrip("0").replace("00:", "0:")
            spazi_validi.append(f"{ora_in}-{ora_fin}")

    return spazi_validi

def estrai_orari_24h(testo):
    orari = re.findall(r'\b\d{1,2}:\d{2}\b', testo)
    if len(orari) >= 2:
        inizio = f"{int(orari[0].split(':')[0]):02d}:{orari[0].split(':')[1]}"
        fine = f"{int(orari[1].split(':')[0]):02d}:{orari[1].split(':')[1]}"
        return inizio, fine
    return None, None

def analizza_disponibilita_aula_web(oid_aula, nome_aula, durata_minima_minuti, num_settimane):
    url_calendario = f"https://offertaformativa.unipa.it/offweb/public/aula/calendar.seam?oidAula={oid_aula}"
    
    opzioni = webdriver.ChromeOptions()
    opzioni.add_argument('--headless')
    opzioni.add_argument('--no-sandbox')
    opzioni.add_argument('--disable-dev-shm-usage')
    opzioni.add_argument('--disable-gpu')
    
    # Su Streamlit Cloud bisogna specificare l'eseguibile di sistema
    driver = webdriver.Chrome(options=opzioni)
    
    ore = durata_minima_minuti / 60
    ore_str = f"{int(ore)}h" if ore.is_integer() else f"{ore}h"
    output_aula = ""
    
    try:
        driver.get(url_calendario)
        for settimana_corrente in range(num_settimane):
            WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CLASS_NAME, "fc-content-col")))
            
            try:
                titolo_periodo = driver.find_element(By.CSS_SELECTOR, ".fc-toolbar h2").text
            except:
                titolo_periodo = f"Settimana {settimana_corrente + 1}"
            
            intestazioni = driver.find_elements(By.CLASS_NAME, "fc-day-header")
            giorni_tradotti = {"Lun": "lunedì", "Mar": "martedì", "Mer": "mercoledì", "Gio": "giovedì", "Ven": "venerdì", "Sab": "sabato", "Dom": "domenica"}
            nomi_giorni = [giorni_tradotti.get(intesta.text.split()[0], intesta.text.split()[0].lower()) for intesta in intestazioni if intesta.text.strip() != ""]
            
            colonne_giorni = driver.find_elements(By.CLASS_NAME, "fc-content-col")
            spazi_trovati_nella_settimana = False
            
            for i, colonna in enumerate(colonne_giorni):
                if i >= len(nomi_giorni): break
                nome_giorno = nomi_giorni[i]
                if nome_giorno in ["sabato", "domenica"]: continue
                    
                eventi_del_giorno = colonna.find_elements(By.CLASS_NAME, "fc-event")
                orari_occupati = []
                for evento in eventi_del_giorno:
                    inizio, fine = estrai_orari_24h(evento.text)
                    if inizio and fine:
                        orari_occupati.append((inizio, fine))
                
                buchi = calcola_spazi_liberi_filtrati(orari_occupati, durata_minima_minuti)
                if buchi:
                    spazi_trovati_nella_settimana = True
                    testo_buchi = " e ".join(buchi)
                    output_aula += f"[{titolo_periodo}] spazi da {ore_str} in {nome_aula}: {nome_giorno} {testo_buchi}\n"

            if not spazi_trovati_nella_settimana and len(colonne_giorni) > 0:
                 eventi_totali = driver.find_elements(By.CLASS_NAME, "fc-event")
                 if len(eventi_totali) == 0:
                     output_aula += f"[{titolo_periodo}] spazi da {ore_str} in {nome_aula}: lunedì-venerdì 8:00-19:00\n"

            if settimana_corrente < num_settimane - 1:
                try:
                    driver.find_element(By.CLASS_NAME, "fc-next-button").click()
                    time.sleep(1.5) 
                except: break 
    except:
        pass
    finally:
        driver.quit()
        
    return output_aula

# --- INTERFACCIA WEB STREAMLIT ---
st.set_page_config(page_title="Prenotazione Aule Unipa", layout="centered")

st.title("Ricerca Aule Libere Unipa")
st.markdown("Strumento per la ricerca di aule disponibili per i recuperi.")

col1, col2 = st.columns(2)
with col1:
    DURATA_LEZIONE_MINUTI = st.number_input("Durata (in minuti)", min_value=30, value=180, step=30)
with col2:
    NUM_SETTIMANE = st.number_input("Settimane da controllare", min_value=1, max_value=4, value=1)

if st.button("Avvia Scansione", type="primary"):
    with st.spinner("Scansione in corso. Potrebbe volerci qualche minuto..."):
        mappa = ottieni_mappa_aule()
        AULE_DA_ESCLUDERE = [556, 656, 657, 47, 43, 44, 45, 248, 829, 29, 41]
        
        risultato_finale = ""
        
        # Barra di progresso opzionale per feedback visivo
        progress_bar = st.progress(0)
        totale_aule = len([a for a in mappa.keys() if a not in AULE_DA_ESCLUDERE])
        aule_analizzate = 0
        
        for id_aula, nome_aula in mappa.items():
            if id_aula in AULE_DA_ESCLUDERE:
                continue
                
            risultato_aula = analizza_disponibilita_aula_web(id_aula, nome_aula, DURATA_LEZIONE_MINUTI, NUM_SETTIMANE)
            if risultato_aula:
                risultato_finale += risultato_aula
                
            aule_analizzate += 1
            progress_bar.progress(aule_analizzate / totale_aule)
        
        if risultato_finale:
            st.success("Scansione completata!")
            st.text_area("Risultati (pronti da copiare):", risultato_finale, height=300)
        else:
            st.warning("Nessuna aula trovata con questi parametri.")
            
        st.markdown("---")
        st.caption("Aule escluse: Caltanissetta, <35 posti, Aula Nicolosi.")
        st.caption("Verificare sempre manualmente dal sito per giorni di vacanza o imprevisti.")