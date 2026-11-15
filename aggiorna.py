#!/usr/bin/env python3
"""Aggiorna l'archivio delle estrazioni (Lotto, SuperEnalotto, VinciCasa).
Non inventa mai dati: legge solo le fonti attive in sources.json, valida, unisce senza duplicati,
non cancella mai lo storico, registra correzioni e discrepanze in data/modifiche.log."""
import json, sys, re, time, datetime as dt, pathlib, urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent
DATA, CFG = ROOT, ROOT / "sources.json"
GIOCHI = {"lotto": (90, 5), "superenalotto": (90, 6), "vincicasa": (40, 5)}
RUOTE = ["Bari", "Cagliari", "Firenze", "Genova", "Milano", "Napoli", "Palermo", "Roma", "Torino", "Venezia", "Nazionale"]
ROMA = ZoneInfo("Europe/Rome")

def ora():
    return dt.datetime.now(ROMA).isoformat(timespec="seconds")

def parse(gioco, testo):
    N, K = GIOCHI[gioco]
    righe, scartate = [], 0
    for raw in testo.splitlines():
        riga = raw.strip()
        if not riga or riga.startswith("#"):
            continue
        p = [s.strip() for s in re.split(r"[;,\t]+", riga) if s.strip()]
        d = p.pop(0)
        m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", d)
        if m:
            d = f"{m[3]}-{m[2]}-{m[1]}"
        try:
            dt.date.fromisoformat(d)
            ruota = ""
            if gioco == "lotto":
                ruota = next((r for r in RUOTE if r.lower() == p.pop(0).lower()), "")
                assert ruota
            v = [int(x) for x in p]
        except (ValueError, AssertionError, IndexError):
            scartate += 1
            continue
        n, x = v[:K], v[K:]
        if len(n) != K or len(set(n)) != K or any(not 1 <= a <= N for a in n) or any(not 1 <= a <= 90 for a in x):
            scartate += 1
            continue
        righe.append({"d": d, "r": ruota, "n": sorted(n), "x": x})
    return righe, scartate

def scarica(url, tentativi=3):
    for i in range(tentativi):
        try:
            rq = urllib.request.Request(url, headers={"User-Agent": "StatisticaBot/1.0 (progetto statistico non commerciale)"})
            with urllib.request.urlopen(rq, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # rete, HTTP, ecc.
            if i == tentativi - 1:
                raise
            time.sleep(5 * 2 ** i)

def unisci(esistenti, per_fonte, log):
    """per_fonte: lista di (nome, ufficiale, righe). Ritorna (archivio, n_nuove, n_corrette)."""
    arch = {(x["d"], x["r"]): x for x in esistenti}
    nuove = corrette = 0
    viste = {}
    for nome, uff, righe in per_fonte:
        for x in righe:
            viste.setdefault((x["d"], x["r"]), []).append((nome, uff, x))
    for k, lista in viste.items():
        ufficiali = [t for t in lista if t[1]]
        scelta = (ufficiali or lista)[0][2]
        accordo = sum(1 for t in lista if t[2]["n"] == scelta["n"])
        ver = "verificato" if accordo >= 2 else ("solo-ufficiale" if ufficiali else "non-verificato")
        if accordo < len(lista):
            log.append({"t": ora(), "tipo": "discrepanza", "chiave": list(k), "fonti": [[t[0], t[2]["n"]] for t in lista]})
        if k not in arch:
            arch[k] = {**scelta, "ver": ver, "src": [t[0] for t in lista], "agg": ora()}
            nuove += 1
        else:
            old = arch[k]
            if ufficiali and old["n"] != scelta["n"]:
                log.append({"t": ora(), "tipo": "correzione", "chiave": list(k), "prima": old["n"], "dopo": scelta["n"]})
                arch[k] = {**scelta, "ver": ver, "src": [t[0] for t in lista], "agg": ora()}
                corrette += 1
            elif old.get("ver") != ver and ver == "verificato":
                old["ver"] = ver
    return sorted(arch.values(), key=lambda x: (x["d"], x["r"])), nuove, corrette

def scrivi(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)

def main():
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    meta_p = DATA / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {"giochi": {}}
    log, cambiato, errori = [], False, 0
    for g in GIOCHI:
        fonti = [f for f in cfg.get(g, []) if f.get("attiva")]
        stato = {"righe": 0, "ultima_data": None, "stato": "nessuna-fonte-attiva", "errore": None}
        path = DATA / f"{g}.json"
        esist = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        per_fonte, falliti = [], []
        for f in fonti:
            try:
                testo = scarica(f["url"]) if f.get("url") else pathlib.Path(ROOT / f["file"]).read_text(encoding="utf-8")
                righe, scart = parse(g, testo)
                if not righe:
                    raise ValueError(f"nessuna riga valida ({scart} scartate)")
                per_fonte.append((f["nome"], bool(f.get("ufficiale")), righe))
            except Exception as e:
                falliti.append(f"{f['nome']}: {e}")
        if per_fonte:
            nuovo, n_new, n_cor = unisci(esist, per_fonte, log)
            if len(nuovo) < len(esist):
                raise SystemExit(f"{g}: l'archivio si ridurrebbe, aggiornamento annullato")
            if n_new or n_cor:
                scrivi(path, nuovo)
                cambiato = True
            esist = nuovo
            stato["stato"] = "ok" if not falliti else "parziale"
        elif fonti:
            stato["stato"] = "errore"
            errori += 1
        stato["righe"] = len(esist)
        stato["ultima_data"] = esist[-1]["d"] if esist else None
        stato["errore"] = "; ".join(falliti) or None
        if meta["giochi"].get(g) != stato:
            cambiato = True
        meta["giochi"][g] = stato
    if cambiato:
        meta["ultimo_aggiornamento"] = ora()
        scrivi(meta_p, meta)
    if log:
        with open(DATA / "modifiche.log", "a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in log)
    print(f"{ora()} cambiato={cambiato} errori={errori}")
    return 1 if errori else 0

if __name__ == "__main__":
    sys.exit(main())
