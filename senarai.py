"""Modul senarai: senarai berpangkat yang dijana mesin (PELAN-senarai-niche §2.1–2.2, D30).

Config satu senarai (`config/senarai/<id>.toml`) + templat bersama (`config/templat/senarai.toml`) → fakta GitHub setiap
entri (bintang, tolakan terakhir, lesen, arkib, fork, penerangan) → pangkat ikut formula config → `README.md` (EN) +
`README.ms.md` (BM). Fail ditulis HANYA bila kandungannya berubah (tiada komit kosong dalam repo senarai).

Fail ini BERDIRI SENDIRI (stdlib, tiada import gilap): ia disalin ke repo senarai dan dijalankan oleh Actions di sana
(`python3 senarai.py bina --config senarai.toml --templat templat.toml --keluar .`). Tiada ayat pembaca di sini —
setiap perkataan awam datang daripada config/templat; mesej dalam fail ini untuk pengendali sahaja.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.client
import json
import math
import os
import re
import subprocess
import sys
import textwrap
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

API = "https://api.github.com"
BAHASA = ("en", "ms")
SYARAT = ("laman_indeks", "repo_awam")
KUNCI_ATAS = {"id", "repo", "en", "ms", "pangkat", "ambang", "bundar_bintang", "buang_fork", "kecuali", "produk",
              "kategori", "entri", "cari", "github", "kategori_prompt", "prompt", "seksyen", "aset", "papar"}
TOPIK_GITHUB = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")  # peraturan topik GitHub (huruf kecil, ≤ 50 aksara)
KUNCI_TEMPLAT = ("fail", "lencana", "pautan_bahasa", "kandungan", "entri", "fakta", "titik", "tiada_lesen",
                 "lesen_lain", "produk", "produk_lalai", "cara_tajuk", "cara", "sumbang_tajuk", "sumbang", "sumbang_fail",
                 "kad_tajuk", "kad")
FAIL_SUMBANG = "contributing.md"  # nama yang awesome-lint `awesome-contributing` wajibkan
NAMA_REPO = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
SEMENTARA = {408, 425, 429, 500, 502, 503, 504}  # D18: gangguan yang biasanya hilang sendiri
RALAT_RANGKAIAN = (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException)  # D29
AKHIR = ".!?…"
# Blok PILIHAN (D66): [[prompt]] + [[seksyen]] + [aset]. Senarai tanpanya = keluaran sama bait seperti sebelum blok ini wujud.
FAIL_HASIL = {"gif": "pratonton.gif", "mp4": "hasil.mp4", "meta": "meta.json"}  # dalam folder `hasil` setiap prompt
KUNCI_ASET = {"pratonton_kb", "video_kb", "meta_kb", "jumlah_mb"}
LEBAR_PROMPT = (30, 120)   # [papar] lebar_prompt: had munasabah balutan baris blok prompt (D70)
LALUAN_HASIL = re.compile(r"^[a-z0-9-]+(/[a-z0-9-]+)*$")   # relatif, tiada `..`
NISBAH = re.compile(r"^[1-9][0-9]?:[1-9][0-9]?$")

AmbilJson = Callable[[str], object]
MuatLaman = Callable[[str], tuple[int, dict, str]]


class RalatSenarai(Exception):
    def __init__(self, mesej: str, kod: int | None = None):
        super().__init__(mesej)
        self.kod = kod


# ── semakan skema (luar talian) ──────────────────────────────────────────────────


def periksa(cfg: dict, templat: dict) -> list[str]:
    """Semua kesilapan config + templat; kosong = sah. Tiada rangkaian (dipanggil gerbang autopilot)."""
    r: list[str] = []
    for k in sorted(set(cfg) - KUNCI_ATAS):
        r.append(f"kunci tidak dikenali: {k}")
    if not re.fullmatch(r"[a-z0-9-]+", str(cfg.get("id", ""))):
        r.append("id mesti huruf kecil/angka/sengkang")
    if cfg.get("repo") and not NAMA_REPO.match(str(cfg["repo"])):
        r.append(f"repo bukan owner/nama: {cfg['repo']}")
    for b in BAHASA:
        for k in ("tajuk", "pembuka"):
            if not str(cfg.get(b, {}).get(k, "")).strip():
                r.append(f"{b}.{k} kosong")
        t = templat.get(b, {})
        for k in KUNCI_TEMPLAT:
            if not isinstance(t.get(k), str):
                r.append(f"templat {b}.{k} tiada")
    # [github] = deskripsi + topik repo senarai: teks awam yang diluluskan bersama tajuk/pembuka (D38), terbit bait-sama.
    gh = cfg.get("github", {})
    for k in sorted(set(gh) - {"penerangan", "topik"}):
        r.append(f"github: kunci tidak dikenali: {k}")
    if gh and not str(gh.get("penerangan", "")).strip():
        r.append("github.penerangan kosong")
    topik = gh.get("topik", [])
    if gh and (not isinstance(topik, list) or not 1 <= len(topik) <= 20):
        r.append("github.topik mesti senarai 1–20 topik")
    for t in topik if isinstance(topik, list) else []:
        if not TOPIK_GITHUB.match(str(t)):
            r.append(f"github.topik tidak sah: {t!r}")
    p, a = cfg.get("pangkat", {}), cfg.get("ambang", {})
    for k in ("bintang", "aktif"):
        if not isinstance(p.get(k), (int, float)) or p[k] < 0:
            r.append(f"pangkat.{k} mesti nombor ≥ 0")
    if not isinstance(a.get("bintang_min"), int) or a["bintang_min"] < 0:
        r.append("ambang.bintang_min mesti integer ≥ 0")
    if not isinstance(a.get("hari_tidak_aktif"), int) or a["hari_tidak_aktif"] <= 0:
        r.append("ambang.hari_tidak_aktif mesti integer > 0")
    if not isinstance(cfg.get("bundar_bintang", 1), int) or cfg.get("bundar_bintang", 1) < 1:
        r.append("bundar_bintang mesti integer ≥ 1")
    elif isinstance(a.get("bintang_min"), int) and cfg.get("bundar_bintang", 1) > max(1, a["bintang_min"]):
        r.append("bundar_bintang > ambang.bintang_min — entri kecil akan tertulis ★ 0+ (D33)")
    kat = cfg.get("kategori", [])
    if not kat:
        r.append("tiada kategori")
    id_kat = [k.get("id") for k in kat]
    if len(set(id_kat)) != len(id_kat):
        r.append("id kategori berulang")
    for k in kat:
        for b in BAHASA:
            if not str(k.get(b, "")).strip():
                r.append(f"kategori {k.get('id')}: tajuk {b} kosong")
    dilihat: set[str] = set()
    for e in cfg.get("entri", []):
        n = str(e.get("repo", ""))
        if not NAMA_REPO.match(n):
            r.append(f"entri bukan owner/nama: {n!r}")
        if n.lower() in dilihat:
            r.append(f"entri berulang: {n}")
        dilihat.add(n.lower())
        if cfg.get("repo") and n.split("/")[0].lower() == str(cfg["repo"]).split("/")[0].lower():
            r.append(f"entri {n}: milik pemilik senarai sendiri — produk sendiri tidak dipangkat (§2.2)")
        if e.get("kategori") not in id_kat:
            r.append(f"entri {n}: kategori tidak dikenali {e.get('kategori')!r}")
    for n in cfg.get("kecuali", []):
        if not NAMA_REPO.match(str(n)):
            r.append(f"kecuali bukan owner/nama: {n!r}")
    c = cfg.get("cari")
    if c is not None:
        q = c.get("pertanyaan")
        if not isinstance(q, list) or not q or not all(isinstance(x, str) and x.strip() for x in q):
            r.append("cari.pertanyaan mesti senarai rentetan tidak kosong")
        if not isinstance(c.get("had", 30), int) or not 1 <= c.get("had", 30) <= 100:
            r.append("cari.had mesti integer 1–100")
    pr = cfg.get("produk")
    if pr is not None:
        if not str(pr.get("nama", "")).strip() or not str(pr.get("url", "")).startswith("https://"):
            r.append("produk perlu nama + url https://")
        b = pr.get("bilangan")
        if b is not None and not (isinstance(b, dict) and all(isinstance(b.get(k), str) and b[k] for k in ("fail", "senarai", "ada"))):
            r.append("produk.bilangan perlu fail + senarai + ada")
        for bh in BAHASA:
            if "{bilangan}" in str(pr.get(bh, "")) and b is None:
                r.append(f"produk.{bh} memakai {{bilangan}} tanpa produk.bilangan")
        if pr.get("syarat") not in SYARAT:
            r.append(f"produk.syarat mesti salah satu {SYARAT}")
        if pr.get("syarat") == "repo_awam" and not NAMA_REPO.match(str(pr.get("repo", ""))):
            r.append("produk.syarat repo_awam perlu produk.repo owner/nama")
        if pr.get("repo") and str(pr["repo"]).lower() in dilihat:
            r.append(f"produk sendiri {pr['repo']} tidak boleh menjadi entri berpangkat (§2.2)")
    r += periksa_prompt(cfg, templat)
    if not r:  # slot templat yang tidak dikenali = KeyError semasa bina; tangkap di sini, bukan di Actions
        contoh = Entri("o/n", "https://github.com/o/n", kat[0]["id"], 1, dt.date(2000, 1, 1), "MIT", "x")
        try:
            jana({**cfg, "produk": {"nama": "x", "url": "https://x"}}, templat, [contoh], True)
        except (KeyError, IndexError, ValueError, AttributeError) as e:
            r.append(f"slot templat tidak dikenali: {e!r}")
    return r


def _teks(d: object, kunci: tuple[str, ...]) -> bool:
    return isinstance(d, dict) and all(isinstance(d.get(k), str) and d[k].strip() for k in kunci)


def periksa_prompt(cfg: dict, templat: dict) -> list[str]:
    """Skema blok pilihan [[kategori_prompt]] / [[prompt]] / [[seksyen]] / [aset] + label [<bahasa>.prompt] + sauh unik."""
    r: list[str] = []
    kp, prompt, seksyen = cfg.get("kategori_prompt", []), cfg.get("prompt", []), cfg.get("seksyen", [])
    for nama, blok, kunci in (("kategori_prompt", kp, {"id", "en", "ms"}), ("prompt", prompt, {"id", "kategori", "en", "ms", "hasil", "nisbah"}),
                              ("seksyen", seksyen, {"id", "en", "ms"})):
        ids = [x.get("id") for x in blok]
        if len(set(ids)) != len(ids):
            r.append(f"id {nama} berulang")
        for x in blok:
            if not re.fullmatch(r"[a-z0-9-]+", str(x.get("id", ""))):
                r.append(f"{nama} {x.get('id')!r}: id mesti huruf kecil/angka/sengkang")
            for k in sorted(set(x) - kunci):
                r.append(f"{nama} {x.get('id')}: kunci tidak dikenali: {k}")
    for k in kp:
        if not all(isinstance(k.get(b), str) and k[b].strip() for b in BAHASA):
            r.append(f"kategori_prompt {k.get('id')}: tajuk kosong")
    id_kp = {k.get("id") for k in kp}
    for p in prompt:
        pid = p.get("id")
        if p.get("kategori") not in id_kp:
            r.append(f"prompt {pid}: kategori tidak dikenali {p.get('kategori')!r}")
        for b in BAHASA:
            for k in sorted(set(p.get(b, {})) - {"tajuk", "prompt"}):
                r.append(f"prompt {pid}.{b}: kunci tidak dikenali: {k}")
            if not _teks(p.get(b), ("tajuk", "prompt")):
                r.append(f"prompt {pid}.{b}: tajuk + prompt wajib")
            elif "```" in p[b]["prompt"]:
                r.append(f"prompt {pid}.{b}: ``` dalam prompt memecah blok kod")
        if "hasil" in p and not LALUAN_HASIL.match(str(p["hasil"])):
            r.append(f"prompt {pid}: hasil mesti laluan relatif huruf kecil (tiada ..)")
        if "nisbah" in p and not NISBAH.match(str(p["nisbah"])):
            r.append(f"prompt {pid}: nisbah mesti bentuk L:T, cth 9:16")
    for s in seksyen:
        for b in BAHASA:
            for k in sorted(set(s.get(b, {})) - {"tajuk", "badan"}):
                r.append(f"seksyen {s.get('id')}.{b}: kunci tidak dikenali: {k}")
            if not _teks(s.get(b), ("tajuk", "badan")):
                r.append(f"seksyen {s.get('id')}.{b}: tajuk + badan wajib")
    if prompt:
        aset = cfg.get("aset")
        if not isinstance(aset, dict) or set(aset) != KUNCI_ASET or not all(
                isinstance(v, int) and v > 0 for v in aset.values()):
            r.append(f"aset wajib bila ada prompt: {', '.join(sorted(KUNCI_ASET))} (integer > 0)")
        for b in BAHASA:
            lb = cfg.get(b, {}).get("prompt")
            for k in sorted(set(lb or {}) - {"hasil", "pengenalan"}):
                r.append(f"{b}.prompt: kunci tidak dikenali: {k}")
            if not _teks(lb, ("hasil",)):
                r.append(f"{b}.prompt.hasil (label hasil) wajib bila ada prompt")
                continue
            try:
                lb["hasil"].format(tajuk="t", gif="g", mp4="m", kod="k")
            except (KeyError, IndexError, ValueError) as e:
                r.append(f"{b}.prompt.hasil: slot tidak dikenali {e!r}")
        papar = cfg.get("papar", {})
        lp = papar.get("lebar_prompt") if isinstance(papar, dict) else None
        if not isinstance(papar, dict) or set(papar) - {"lebar_prompt"} or (lp is not None and not (
                isinstance(lp, int) and LEBAR_PROMPT[0] <= lp <= LEBAR_PROMPT[1])):
            r.append(f"papar: hanya lebar_prompt (integer {LEBAR_PROMPT[0]}–{LEBAR_PROMPT[1]})")
    elif cfg.get("aset") is not None or cfg.get("papar") is not None or kp:
        r.append("aset / papar / kategori_prompt tanpa sebarang prompt")
    if r or not (prompt or seksyen):
        return r
    for b in BAHASA:   # sauh = slug tajuk; GitHub menambah -1 pada pendua → pautan Threads (K11) tersasar
        t = templat.get(b, {})
        tajuk = [cfg[b]["tajuk"], t.get("kandungan", ""), t.get("cara_tajuk", ""), t.get("kad_tajuk", ""),
                 t.get("sumbang_tajuk", "")] + [str(k.get(b, "")) for k in kp + cfg.get("kategori", [])]
        tajuk += [p[b]["tajuk"] for p in prompt] + [s[b]["tajuk"] for s in seksyen]
        sauh = [slug(h) for h in tajuk]
        for d in sorted({x for x in sauh if sauh.count(x) > 1}):
            r.append(f"sauh berulang ({b}): #{d}")
    return r


# ── fakta ──────────────────────────────────────────────────────────────────────


@dataclass
class Entri:
    nama: str
    url: str
    kategori: str
    bintang: int
    tolak: dt.date
    lesen: str | None  # SPDX; "NOASSERTION" = lesen lain; None = tiada
    penerangan: str
    skor: float = 0.0


def pembuat_ambil_json(token: str | None, timeout: float = 30, cuba: int = 3, rehat: float = 3.0,
                       tidur=time.sleep) -> AmbilJson:
    """GET JSON dari API GitHub; gangguan (D18/D29) dicuba semula; 404 = RalatSenarai kod 404 serta-merta."""
    h = {"User-Agent": "gilap-senarai", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"

    def sekali(url: str) -> object:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise RalatSenarai(f"HTTP {e.code} {url}", kod=e.code) from e
        except (json.JSONDecodeError, *RALAT_RANGKAIAN) as e:
            raise RalatSenarai(f"{type(e).__name__}: {getattr(e, 'reason', e)} {url}") from e

    def ambil(url: str) -> object:
        for i in range(cuba):
            try:
                return sekali(url)
            except RalatSenarai as e:
                if e.kod not in SEMENTARA | {None} or i == cuba - 1:
                    raise
            tidur(rehat)
        raise AssertionError("tidak tercapai")

    return ambil


def cari(cfg: dict, ambil: AmbilJson) -> list[dict]:
    """Calon BAHARU dari carian GitHub (`[cari] pertanyaan`): lulus ambang bintang, ada penerangan, bukan fork/arkib,
    belum dalam entri/kecuali/produk. Untuk kurasi manusia/agen — calon TIDAK masuk senarai dengan sendirinya."""
    from urllib.parse import quote

    c, a = cfg.get("cari") or {}, cfg["ambang"]
    sudah = {str(e["repo"]).lower() for e in cfg.get("entri", [])} | {str(n).lower() for n in cfg.get("kecuali", [])}
    if (cfg.get("produk") or {}).get("repo"):
        sudah.add(str(cfg["produk"]["repo"]).lower())
    calon: dict[str, dict] = {}
    for q in c.get("pertanyaan", []):
        jawab = ambil(f"{API}/search/repositories?q={quote(q)}&sort=stars&order=desc&per_page={c.get('had', 30)}")
        for i in jawab.get("items", []):
            n = i["full_name"]
            if (n.lower() in sudah or i.get("fork") or i.get("archived") or not (i.get("description") or "").strip()
                    or i["stargazers_count"] < a["bintang_min"]):
                continue
            calon.setdefault(n, {"repo": n, "bintang": i["stargazers_count"], "tolak": i["pushed_at"][:10],
                                 "lesen": (i.get("license") or {}).get("spdx_id"), "penerangan": i["description"],
                                 "pertanyaan": []})["pertanyaan"].append(q)
    return sorted(calon.values(), key=lambda x: (-x["bintang"], x["repo"].lower()))


def kumpul(cfg: dict, ambil: AmbilJson) -> tuple[list[Entri], list[tuple[str, str]]]:
    """(entri yang lulus, [(repo, sebab dibuang)]). Gangguan selain 404 NAIK — senarai tidak pernah ditulis dengan
    entri yang hilang kerana rangkaian."""
    a = cfg["ambang"]
    kecuali = {n.lower() for n in cfg.get("kecuali", [])}
    lulus, buang = [], []
    for e in cfg.get("entri", []):
        n = e["repo"]
        if n.lower() in kecuali:
            buang.append((n, "kecuali"))
            continue
        try:
            i = ambil(f"{API}/repos/{n}")
        except RalatSenarai as x:
            if x.kod != 404:
                raise
            buang.append((n, "tiada (404)"))
            continue
        lesen = (i.get("license") or {}).get("spdx_id")
        en = Entri(i["full_name"], f"https://github.com/{i['full_name']}", e["kategori"], int(i["stargazers_count"]),
                   dt.date.fromisoformat(i["pushed_at"][:10]), lesen, bersih(i.get("description") or ""))
        sebab = ("diarkib" if i.get("archived") else "fork" if i.get("fork") and cfg.get("buang_fork", True)
                 else "peribadi" if i.get("private") else "tiada penerangan" if not en.penerangan
                 else f"bintang < {a['bintang_min']}" if en.bintang < a["bintang_min"] else None)
        if sebab:
            buang.append((n, sebab))
        else:
            lulus.append(en)
    return lulus, buang


def bersih(teks: str) -> str:
    """Penerangan dipetik apa adanya; hanya ruang dirapatkan dan aksara markdown/HTML dilepaskan (bukan disunting)."""
    teks = " ".join("".join(c for c in teks if c.isprintable()).split())
    return re.sub(r"([\\`*_\[\]<>|#])", r"\\\1", teks)


def syarat_produk(pr: dict | None, ambil: AmbilJson, muat: MuatLaman) -> bool:
    """§2.2: baris produk sendiri hanya bila syaratnya BENAR pada larian ini. Apa-apa ralat = tidak (gagal-tertutup)."""
    if not pr:
        return False
    try:
        if pr["syarat"] == "repo_awam":
            return ambil(f"{API}/repos/{pr['repo']}").get("private") is False
        return laman_boleh_indeks(pr["url"], muat)
    except (RalatSenarai, *RALAT_RANGKAIAN, KeyError, ValueError, AttributeError):
        return False


def bilangan_produk(pr: dict, ambil: AmbilJson) -> int | None:
    """Angka dalam blok produk dikira daripada repo produk itu sendiri (cth adapter dalam registry bayarkit), tidak ditaip.
    Gagal membaca = None → blok tidak dipapar (gagal-tertutup: tiada angka basi atau rekaan)."""
    b = pr["bilangan"]
    try:
        isi = ambil(f"{API}/repos/{pr['repo']}/contents/{b['fail']}")
        data = json.loads(base64.b64decode(isi["content"]).decode("utf-8"))
        return sum(1 for x in data[b["senarai"]] if x.get(b["ada"]))
    except (RalatSenarai, *RALAT_RANGKAIAN, KeyError, ValueError, TypeError, AttributeError):
        return None


def laman_boleh_indeks(url: str, muat: MuatLaman) -> bool:
    kod, peng, html = muat(url)
    if kod != 200 or "noindex" in str({k.lower(): v for k, v in peng.items()}.get("x-robots-tag", "")).lower():
        return False
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        if re.search(r"name\s*=\s*[\"']?(robots|googlebot)", m.group(0), re.I) and "noindex" in m.group(0).lower():
            return False
    kod, _, robots = muat(url.rstrip("/") + "/robots.txt")
    return kod == 404 or (kod == 200 and not robots_sekat_semua(robots))


def robots_sekat_semua(robots: str) -> bool:
    """`Disallow: /` dalam kumpulan `User-agent: *` (atau Googlebot) = laman tidak boleh diindeks."""
    agen, sekat, baru = set(), False, False
    for baris in robots.splitlines():
        k, _, v = baris.split("#", 1)[0].partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            if not baru:
                agen = set()
            agen.add(v.lower())
            baru = True
        elif k in ("disallow", "allow"):
            baru = False
            if k == "disallow" and v == "/" and agen & {"*", "googlebot"}:
                sekat = True
    return sekat


def muat_laman(url: str, timeout: float = 30) -> tuple[int, dict, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "gilap-senarai"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), ""


# ── pangkat + render ───────────────────────────────────────────────────────────


def pangkat(entri: list[Entri], cfg: dict, hari_ini: dt.date) -> list[Entri]:
    """Buang yang tidak aktif melebihi ambang, kira skor, susun (skor ↓, bintang ↓, nama)."""
    p, had = cfg["pangkat"], cfg["ambang"]["hari_tidak_aktif"]
    hasil = []
    for e in entri:
        hari = (hari_ini - e.tolak).days
        if hari > had:
            continue
        e.skor = p["bintang"] * math.log10(1 + e.bintang) + p["aktif"] * max(0.0, 1 - hari / had)
        hasil.append(e)
    return sorted(hasil, key=lambda e: (-round(e.skor, 9), -e.bintang, e.nama.lower()))


def bintang(n: int, bundar: int) -> str:
    """Dibundar ke BAWAH (README stabil minggu ke minggu); `+` bila nilai sebenar lebih besar."""
    if n >= 1000:
        k = math.floor(n / 100) / 10
        teks = f"{k:g}k"
        return teks + ("+" if k * 1000 < n else "")
    b = n - n % bundar
    return f"{b}" + ("+" if b < n else "")


def slug(tajuk: str) -> str:
    """Anchor tajuk GitHub: huruf kecil, buang tanda baca (kecuali - _), ruang → -."""
    return re.sub(r"[^\w\- ]", "", tajuk.strip().lower()).replace(" ", "-")


def jana(cfg: dict, templat: dict, entri: list[Entri], produk: bool | dict) -> dict[str, str]:
    """{nama fail: kandungan} untuk setiap bahasa. Tiada masa jana dalam teks — fail berubah hanya bila fakta berubah."""
    p, a = cfg["pangkat"], cfg["ambang"]
    bundar = cfg.get("bundar_bintang", 1)
    keluar = {}
    for b in BAHASA:
        t, s = templat[b], cfg[b]
        bahagian = bahagian_prompt(cfg, b)
        for k in cfg["kategori"]:
            milik = [e for e in entri if e.kategori == k["id"]]
            if not milik:
                continue
            baris = []
            for e in milik:
                lesen = t["tiada_lesen"] if not e.lesen else t["lesen_lain"] if e.lesen == "NOASSERTION" else e.lesen
                fakta = t["fakta"].format(bintang=bintang(e.bintang, bundar), lesen=lesen, aktif=e.tolak.strftime("%Y-%m"))
                pen = e.penerangan if e.penerangan[-1] in AKHIR else e.penerangan + t["titik"]
                baris.append(t["entri"].format(nama=e.nama, url=e.url, fakta=fakta, penerangan=pen))
            bahagian.append((k[b], "\n".join(baris)))
        cara = t["cara"].format(w_bintang=f"{p['bintang']:g}", w_aktif=f"{p['aktif']:g}", hari=a["hari_tidak_aktif"],
                                bintang_min=a["bintang_min"], bundar=bundar)
        # Kandungan tidak memaut "menyumbang" (awesome-lint `awesome-toc`); tiada bahagian lesen (`awesome-license`).
        bahagian += [(x[b]["tajuk"], x[b]["badan"].strip()) for x in cfg.get("seksyen", [])]   # sebelum bahagian cara pangkat
        tetap = [(t["cara_tajuk"], cara), (t["kad_tajuk"], t["kad"])]   # awesome-toc: setiap ## selain Contributing dalam Kandungan
        isi = [f"# {s['tajuk']} {t['lencana']}", t["pautan_bahasa"], f"> {s['pembuka']}"]
        if produk:
            pr = cfg["produk"]
            ganti = {"nama": pr["nama"], "url": pr["url"], **(produk if isinstance(produk, dict) else {})}
            isi.append(t["produk"].format(teks=(pr.get(b) or t["produk_lalai"]).format(**ganti)))
        if cfg.get("prompt") and s["prompt"].get("pengenalan"):
            isi.append(s["prompt"]["pengenalan"].strip())
        isi.append(f"## {t['kandungan']}")
        isi.append("\n".join(_kandungan(h, badan) for h, badan in bahagian + tetap))
        for h, badan in bahagian + tetap + [(t["sumbang_tajuk"], t["sumbang"])]:
            isi += [f"## {h}", badan]
        keluar[t["fail"]] = "\n\n".join(isi) + "\n"
    # Satu contributing.md dua bahasa (awesome-lint mewajibkan nama ini); kriteria = nombor config yang sama.
    keluar[FAIL_SUMBANG] = "\n\n".join(
        templat[b]["sumbang_fail"].format(bintang_min=a["bintang_min"], hari=a["hari_tidak_aktif"]).strip()
        for b in BAHASA) + "\n"
    return keluar


def _kandungan(h: str, badan: str) -> str:
    """Baris Kandungan satu bahagian; tajuk ### di dalamnya (prompt) dipaut bersarang supaya setiap prompt ada pautan terus."""
    return "\n".join([f"- [{h}](#{slug(h)})"] + [f"  - [{x}](#{slug(x)})" for x in re.findall(r"^### (.+)$", badan, re.M)])


def bahagian_prompt(cfg: dict, b: str) -> list[tuple[str, str]]:
    """[(tajuk kategori_prompt, badan)] — setiap prompt = ### tajuk + blok ```text + pratonton/pautan bila `hasil` ada.
    Kategori tanpa prompt tidak dipapar. Label datang dari [<bahasa>.prompt] config senarai (bukan templat bersama)."""
    keluar, lebar = [], cfg.get("papar", {}).get("lebar_prompt")
    for k in cfg.get("kategori_prompt", []):
        blok = []
        for p in (x for x in cfg.get("prompt", []) if x["kategori"] == k["id"]):
            isi = [f"### {p[b]['tajuk']}", f"```text\n{balut(p[b]['prompt'].strip(), lebar)}\n```"]
            if p.get("hasil"):
                d = p["hasil"]
                isi.append(cfg[b]["prompt"]["hasil"].format(
                    tajuk=p[b]["tajuk"], gif=f"{d}/{FAIL_HASIL['gif']}", mp4=f"{d}/{FAIL_HASIL['mp4']}", kod=d))
            blok.append("\n\n".join(isi))
        if blok:
            keluar.append((k[b], "\n\n".join(blok)))
    return keluar


def balut(teks: str, lebar: int | None) -> str:
    """Balut setiap baris prompt pada `lebar` aksara ([papar] lebar_prompt, D70): GitHub tidak membalut blok kod, jadi baris
    panjang menatal mendatar dalam kotaknya. Tiada lebar = teks asal. Perkataan tidak dipecah; setiap baris asal jadi
    perenggan (dipisah baris kosong) supaya struktur prompt kekal kelihatan selepas dibalut."""
    if not lebar:
        return teks
    return "\n\n".join(textwrap.fill(b, lebar, break_long_words=False, break_on_hyphens=False)
                       for b in teks.split("\n") if b.strip())


def tulis(folder: Path, fail: dict[str, str]) -> list[str]:
    """Tulis hanya fail yang kandungannya berbeza; pulangkan nama yang ditulis (kosong = tiada perubahan)."""
    folder.mkdir(parents=True, exist_ok=True)
    ditulis = []
    for nama, isi in fail.items():
        laluan = folder / nama
        if laluan.exists() and laluan.read_text(encoding="utf-8") == isi:
            continue
        tmp = laluan.with_name(f".{nama}.tmp")
        tmp.write_text(isi, encoding="utf-8")
        os.replace(tmp, laluan)
        ditulis.append(nama)
    return ditulis


def muat_toml(laluan: Path) -> dict:
    with open(laluan, "rb") as f:
        return tomllib.load(f)


@dataclass
class Hasil:
    entri: list[Entri]
    buang: list[tuple[str, str]]
    produk: bool
    ditulis: list[str]


def bina(cfg: dict, templat: dict, keluar: Path, ambil: AmbilJson, muat: MuatLaman, hari_ini: dt.date) -> Hasil:
    salah = periksa(cfg, templat)
    if salah:
        raise RalatSenarai("config tidak sah: " + "; ".join(salah))
    lulus, buang = kumpul(cfg, ambil)
    berpangkat = pangkat(lulus, cfg, hari_ini)
    kekal = {id(e) for e in berpangkat}
    buang += [(e.nama, f"tiada tolakan > {cfg['ambang']['hari_tidak_aktif']} hari") for e in lulus if id(e) not in kekal]
    pr: bool | dict = syarat_produk(cfg.get("produk"), ambil, muat)
    if pr and cfg["produk"].get("bilangan"):
        n = bilangan_produk(cfg["produk"], ambil)
        pr = {"bilangan": n} if n else False
    return Hasil(berpangkat, buang, bool(pr), tulis(keluar, jana(cfg, templat, berpangkat, pr)))


def token() -> str | None:
    """Actions: GITHUB_TOKEN. Hos: `gh auth token` semasa larian. Tiada = API tanpa token (had 60/jam)."""
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def dari_fixture(laluan: Path) -> tuple[AmbilJson, MuatLaman]:
    """Fakta luar talian: {"repos": {"o/n": <jawapan API>}, "laman": {"url": [kod, {pengepala}, "badan"]}}."""
    data = json.loads(laluan.read_text(encoding="utf-8"))
    repos = {k.lower(): v for k, v in data.get("repos", {}).items()}

    def ambil(url: str) -> object:
        n = url.removeprefix(f"{API}/repos/").lower()
        if n not in repos:
            raise RalatSenarai(f"HTTP 404 {url}", kod=404)
        return repos[n]

    def muat(url: str) -> tuple[int, dict, str]:
        kod, peng, badan = data.get("laman", {}).get(url, [404, {}, ""])
        return kod, peng, badan

    return ambil, muat


def laporan(h: Hasil) -> str:
    baris = [f"{len(h.entri)} entri berpangkat · baris produk: {'ya' if h.produk else 'tidak'} · "
             f"ditulis: {', '.join(h.ditulis) or 'tiada perubahan'}"]
    baris += [f"  dibuang {n}: {s}" for n, s in h.buang]
    return "\n".join(baris)


def laporan_cari(calon: list[dict]) -> str:
    baris = [f"{len(calon)} calon baharu (kurasi: tambah [[entri]] atau masukkan ke kecuali)"]
    baris += [f"  {c['bintang']:>6}★ {c['tolak']} {c['lesen'] or '-':12} {c['repo']} — {c['penerangan'][:100]}"
              for c in calon]
    return "\n".join(baris)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="senarai.py")
    sub = ap.add_subparsers(dest="arahan", required=True)
    for nama in ("bina", "periksa", "cari"):
        s = sub.add_parser(nama)
        s.add_argument("--config", required=True)
        s.add_argument("--templat", required=True)
        if nama == "bina":
            s.add_argument("--keluar", required=True)
            s.add_argument("--fakta", help="fixture JSON (luar talian)")
    args = ap.parse_args(argv)
    cfg, templat = muat_toml(Path(args.config)), muat_toml(Path(args.templat))
    if args.arahan == "periksa":
        salah = periksa(cfg, templat)
        print("\n".join(f"✗ {s}" for s in salah) or f"✓ {args.config}")
        return 1 if salah else 0
    if args.arahan == "cari":
        print(laporan_cari(cari(cfg, pembuat_ambil_json(token()))))
        return 0
    ambil, muat = dari_fixture(Path(args.fakta)) if args.fakta else (pembuat_ambil_json(token()), muat_laman)
    try:
        h = bina(cfg, templat, Path(args.keluar), ambil, muat, dt.datetime.now(dt.timezone.utc).date())
    except RalatSenarai as e:
        print(f"✗ {e} — tiada fail ditulis")
        return 1
    print(laporan(h))
    return 0


if __name__ == "__main__":
    sys.exit(main())
