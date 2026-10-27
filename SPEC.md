# SPESIFIKASI PROYEK v2.1
## Shorts Engine — Produksi YouTube Shorts Sains yang Dibimbing Sistem

**Versi:** 2.1
**Tanggal:** 22 Oktober 2026
**Menggantikan:** v2.0 (21 Oktober 2026) dan v1.0
**Pemilik:** Developer solo
**Bahasa konten:** Inggris (channel pertama). Bahasa Indonesia sebagai channel kedua di fase lanjutan.
**Bahasa antarmuka aplikasi:** Indonesia
**Niche:** Sains alam — hewan, laut, alam, dan fenomena sehari-hari

### Perubahan dari v2.0
- Mode kerja berubah dari "co-pilot" menjadi **"dibimbing sistem"**: AI memegang topik, riset, verifikasi, naskah, footage, dan render. Manusia hanya merekam narasi, menonton hasil sekali, lalu upload.
- Verifikasi fakta menjadi **otomatis dengan aturan ketat**; klaim yang tidak lolos dibuang, bukan ditanyakan ke manusia.
- **Antrean topik otomatis** dengan cek ketersediaan footage sebelum topik dipakai.
- Pemilihan footage otomatis dengan penilaian visual (vision) pada thumbnail kandidat.
- Halaman utama UI menjadi **"Tugas Hari Ini"**.
- Bahasa konten dapat dikonfigurasi; default `en`.
- Tambahan field **seri** dan penomoran episode.

---

## 0. Ringkasan

Shorts Engine adalah aplikasi lokal di Windows yang memproduksi YouTube Shorts edukatif sains dari ide sampai paket siap upload, dengan **manusia hanya melakukan tiga tugas sederhana:**

1. 🎙️ **Merekam narasi** dari naskah yang ditampilkan sistem (mode teleprompter).
2. 👀 **Menonton hasil akhir sekali** dan menekan *Setuju* atau *Perbaiki*.
3. ⬆️ **Mengunggah** ke YouTube dengan judul dan deskripsi yang tinggal disalin.

Semua hal lain (memilih topik, riset, verifikasi fakta, naskah, subtitle, footage, render, pemeriksaan kualitas, judul, deskripsi, kredit) dikerjakan dan diputuskan oleh sistem. Pengguna tidak perlu memikirkan isi video.

**Target operasional:** ≤ 20 menit kerja manusia per video. Pengguna dapat merekam beberapa naskah sekaligus dalam satu sesi.

**Tujuan proyek (urut prioritas):**
1. Menghasilkan konten layak tayang untuk channel baru berbahasa Inggris.
2. Sarana belajar membangun sistem AI end-to-end.
3. Portofolio teknis.

Target bisnis jangka panjang (Rp15 juta/bulan) **bukan** persyaratan teknis. Sistem tidak boleh mengklaim menjamin monetisasi.

---

## 1. Prinsip desain

1. **Manusia hanya melakukan yang tidak bisa digantikan:** suara asli dan penilaian akhir satu kali tonton. Sistem tidak boleh meminta manusia memutuskan hal teknis atau isi.
2. **Fakta harus bisa diaudit.** Tidak ada klaim di naskah tanpa sumber terverifikasi otomatis. Model AI tidak pernah dianggap sebagai sumber. Jika ragu, buang klaimnya.
3. **Gagal dengan tenang.** Jika sebuah topik tidak cukup bukti atau footage, sistem membuangnya dan mengambil topik berikutnya tanpa mengganggu pengguna.
4. **Sederhana dulu.** Satu proses Python, SQLite, tanpa Redis/Celery/Docker/microservices.
5. **Mesin terpisah dari tampilan.** Seluruh pipeline dapat dijalankan dari CLI.
6. **Provider dan bahasa bisa diganti** lewat konfigurasi.
7. **Biaya terkendali.** Setiap panggilan berbayar dicatat dan dibatasi anggaran.
8. **Bertahap dan teruji.** Satu fase selesai, teruji, dan di-commit sebelum fase berikutnya.

---

## 2. Bukan tujuan (di luar cakupan MVP)

- Upload otomatis ke YouTube (upload manual; sistem menyiapkan paket).
- TTS / suara sintetis.
- Analitik otomatis dari YouTube API (cukup catatan manual sampai ±30 video).
- Visual programatik kompleks (diagram/animasi). MVP hanya teks overlay sederhana.
- Musik latar.
- Channel kedua (Bahasa Indonesia).
- Multi-user, login, atau deployment online.
- Produksi terjadwal tanpa pengguna menjalankan aplikasi.

---

## 3. Lingkungan target

| Komponen | Spesifikasi |
|---|---|
| OS | Windows 10/11 64-bit |
| CPU | Intel Core i5 |
| RAM | 16 GB |
| GPU | NVIDIA RTX 3050, 6 GB VRAM (CUDA + NVENC) |
| Python | 3.12 (dikelola dengan `uv`) |
| Node.js | Versi LTS yang didukung template frontend |
| Media | FFmpeg + FFprobe dengan `libass` |

`shorts doctor` wajib mendeteksi GPU, VRAM, dukungan CUDA, dan encoder `h264_nvenc`, lalu memilih konfigurasi yang sesuai. Jika GPU tidak tersedia, sistem jatuh ke mode CPU dengan peringatan jelas.

---

## 4. Keputusan teknis

| Area | Keputusan | Catatan |
|---|---|---|
| Backend | FastAPI | REST API, SSE, menyajikan build frontend |
| ORM & migrasi | SQLAlchemy 2 + Alembic | |
| Database | SQLite (mode WAL) | |
| Validasi | Pydantic v2 | Semua output LLM divalidasi skema |
| Env Python | `uv` | |
| CLI | Typer | |
| HTTP client | `httpx` | Timeout wajib |
| Job runner | Worker thread tunggal + antrean di SQLite | Tanpa Celery/Redis |
| Progres real-time | Server-Sent Events (SSE) | |
| LLM berat | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | Ekstraksi klaim, naskah, pemeriksaan naskah |
| LLM ringan | Claude Haiku 5.5 (`claude-haiku-5-5`) | Ide topik, query, pemeriksaan dukungan klaim, penilaian thumbnail (vision), judul/deskripsi |
| Search | Tavily (Search + Extract) | Cadangan: Exa |
| Footage | Pexels API | |
| ASR / alignment | `faster-whisper`, default `large-v3-turbo` | Fallback otomatis sesuai VRAM |
| Render final | FFmpeg `libx264` | |
| Render preview | FFmpeg `h264_nvenc` | |
| Frontend | React + Vite + TypeScript + Tailwind, berbasis Tailwick (React TS) | Repo privat |
| Testing | `pytest`; Vitest opsional | |

Nama model, harga per token, dan endpoint disimpan di konfigurasi, bukan di kode.

---

## 5. Repositori dan lisensi

Lisensi Envato Elements melarang redistribusi source template, termasuk yang sudah dimodifikasi.

| Repo | Isi | Visibilitas |
|---|---|---|
| `shorts-engine` | Backend, pipeline, CLI, tes, dokumentasi | **Publik** |
| `shorts-engine-ui` | Frontend berbasis Tailwick | **Privat** |

- File Tailwick **tidak boleh** masuk ke `shorts-engine`.
- Repo publik menampilkan UI lewat screenshot/GIF/video demo.
- Backend menyajikan build frontend dari `FRONTEND_DIST_DIR`.
- Lisensi Tailwick didaftarkan untuk proyek ini; buktinya disimpan di luar repo.

---

## 6. Struktur repo backend

```text
shorts-engine/
├── pyproject.toml
├── uv.lock
├── README.md
├── CLAUDE.md
├── SPEC.md
├── .env.example
├── .gitignore
├── alembic.ini
├── migrations/
├── config/
│   ├── settings.yaml            # pengaturan nonrahasia
│   ├── source_whitelist.yaml    # domain sumber per tier
│   ├── topic_policy.yaml        # kategori, seri, topik terlarang
│   └── style_guide.md           # persona & gaya naskah
├── assets/fonts/                # font subtitle berlisensi bebas
├── docs/
│   ├── decisions/               # ADR
│   └── phase-reports/
├── src/shorts_engine/
│   ├── config.py
│   ├── cli.py
│   ├── worker.py
│   ├── db/
│   ├── domain/                  # state machine, skema Pydantic
│   ├── providers/
│   │   ├── llm/                 # base.py, anthropic.py
│   │   ├── search/              # base.py, tavily.py
│   │   ├── footage/             # base.py, pexels.py
│   │   └── asr/                 # base.py, faster_whisper.py
│   ├── pipeline/
│   │   ├── topics.py
│   │   ├── research.py
│   │   ├── verify.py
│   │   ├── script.py
│   │   ├── voice.py
│   │   ├── align.py
│   │   ├── subtitles.py
│   │   ├── footage.py
│   │   ├── render.py
│   │   ├── quality.py
│   │   └── package.py
│   ├── services/                # jobs, costs, approvals, artifacts, tasks
│   ├── api/
│   └── utils/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── data/                        # di-.gitignore
```

Folder per pekerjaan: `data/jobs/<job_id>/` berisi `research/`, `script/`, `audio/`, `subtitles/`, `footage/`, `render/`, `export/`.

---

## 7. Alur kerja

⚙️ = sistem. 👤 = pengguna.

### 7.1 Antrean topik ⚙️
- Sistem menjaga antrean berisi minimal `topic_queue_min` topik siap (default 10).
- Haiku mengusulkan kandidat topik per kategori dan seri sesuai `topic_policy.yaml` dan `style_guide.md`.
- Setiap kandidat difilter:
  1. **Kebijakan topik:** tolak topik di daftar terlarang (nasihat medis/kesehatan, politik, sains yang masih diperdebatkan, instruksi berbahaya, peristiwa terkini, tragedi).
  2. **Duplikat:** tolak jika mirip dengan topik yang sudah ada atau sudah diproduksi (kemiripan teks; embedding opsional di fase lanjutan). Variasi judul bukan variasi substansi.
  3. **Ketersediaan footage:** jalankan 2–3 query Pexels; tolak jika kandidat video yang cocok kurang dari ambang konfigurasi.
- Topik yang lolos diberi skor (potensi visual, kejutan hook, keragaman kategori) dan masuk antrean.
- 👤 Opsional: pengguna dapat menekan "lewati" pada topik. Pengguna **tidak wajib** meninjau antrean.

### 7.2 Riset ⚙️
- Haiku menyusun 3–6 pertanyaan riset dan query pencarian bahasa Inggris.
- Tavily Search mencari; hasil diberi tier sesuai `source_whitelist.yaml`:
  - **Tier A:** lembaga ilmiah, universitas, jurnal, lembaga pemerintah (mis. NASA, NOAA, Smithsonian, situs `.edu`).
  - **Tier B:** media sains dan ensiklopedia bereputasi (mis. National Geographic, Britannica, BBC Earth).
  - **Tier C:** lainnya. Tidak boleh dipakai sebagai pendukung klaim.
  - Wikipedia hanya untuk menemukan sumber, tidak pernah sebagai pendukung klaim.
- Tavily Extract mengambil isi 3–6 halaman tier A/B teratas.
- URL hanya berasal dari respons API search, tidak pernah dari keluaran LLM.

### 7.3 Verifikasi klaim otomatis ⚙️
Sonnet mengekstrak kandidat klaim, masing-masing dengan kutipan pendukung dari teks sumber. Sebuah klaim berstatus `verified` hanya jika **semua** aturan berikut terpenuhi:

1. **Kutipan ditemukan:** kutipan cocok dengan teks sumber yang diambil (pencocokan ternormalisasi, kemiripan ≥ ambang konfigurasi, default 0,92).
2. **Sumber tier A atau B.**
3. **Kutipan mendukung klaim:** pemeriksaan kedua oleh model terpisah (Haiku) dengan jawaban terstruktur `supports` / `partial` / `no`. Hanya `supports` yang lolos.
4. **Angka dan nama:** setiap angka, satuan, dan nama spesies di klaim muncul di kutipan.
5. **Klaim utama hook** wajib didukung oleh **≥ 2 sumber dari domain berbeda**.

Klaim yang gagal berstatus `rejected` dengan alasan tercatat. Jika jumlah klaim `verified` kurang dari minimum (default 3) atau klaim hook tidak lolos, topik dibuang (`dropped_insufficient_evidence`) dan sistem melanjutkan ke topik berikutnya **tanpa memberi tugas ke pengguna**.

### 7.4 Naskah ⚙️
- Sonnet menulis naskah dalam `content_language` (default `en`) berdasarkan klaim `verified`, `style_guide.md`, dan struktur cerita seri.
- Output terstruktur (Pydantic): `title_working`, `hook`, `segments[]` (teks narasi, `claim_ids[]` atau `kind: framing`, saran visual), `ending`, `estimated_duration_s`, `story_structure`, `series`, `episode_number`.
- Setiap kalimat faktual wajib merujuk minimal satu `claim_id`. Kalimat tanpa fakta (pembuka, transisi, penutup) ditandai `framing`.
- **Pemeriksaan naskah otomatis:**
  - Angka, satuan, dan nama di naskah harus ada di klaim yang dirujuk.
  - Model terpisah memeriksa setiap kalimat faktual terhadap klaimnya (`consistent` / `overstated` / `unsupported`). Hanya `consistent` yang lolos.
  - Tidak ada kalimat berlebihan ("the only", "always", "never", superlatif) tanpa klaim yang mendukung secara eksplisit.
  - Durasi estimasi dalam rentang target (default 35–55 detik; kecepatan bicara dikonfigurasi, default 2,6 kata/detik untuk bahasa Inggris).
- Jika pemeriksaan gagal, naskah ditulis ulang maksimal 2 kali; jika masih gagal, topik dibuang.
- Naskah yang lolos berstatus siap direkam.

### 7.5 Rekaman narasi 👤
- Halaman **Tugas Hari Ini** menampilkan naskah yang siap direkam dalam **mode teleprompter** (teks besar, satu kalimat per baris, penanda jeda, catatan pelafalan untuk istilah sulit seperti nama spesies).
- Sistem dapat menyiapkan beberapa naskah sekaligus agar pengguna merekam dalam satu sesi.
- 👤 Pengguna merekam (di HP atau aplikasi lain) dan mengunggah file per naskah (WAV, M4A, MP3).
- Fase lanjutan: rekam langsung di browser.

### 7.6 Pemrosesan audio ⚙️
- Validasi file dengan FFprobe (durasi, sample rate, kanal).
- Konversi ke WAV mono 48 kHz, potong hening awal/akhir, normalisasi loudness dua tahap (`loudnorm`, −14 LUFS terintegrasi, true peak −1 dBTP). Peredam bising (`afftdn`) opsional, default mati.
- **Pemeriksaan kecocokan rekaman:** transkrip Whisper dibandingkan dengan naskah. Jika kemiripan di bawah ambang (mis. pengguna salah membaca naskah atau ada kalimat terlewat), sistem meminta rekam ulang dengan pesan jelas yang menunjukkan bagian yang berbeda.
- Jika durasi di luar rentang target, sistem memberi tahu pengguna dengan saran sederhana ("bacakan sedikit lebih lambat").

### 7.7 Alignment dan subtitle ⚙️
- `faster-whisper` mentranskripsi audio dengan timestamp per kata (bahasa sesuai `content_language`).
- **Teks subtitle diambil dari naskah** (ejaan benar), **timing dari Whisper.** Pemetaan memakai sequence alignment pada token ternormalisasi.
- Kata yang tidak terpetakan dengan yakin ditandai `timing_uncertain` dan timing-nya diinterpolasi dari kata di sekitarnya.
- Subtitle ditulis sebagai file **ASS** dengan **highlight per kata**, 1–2 baris, maksimal ±32 karakter per baris, posisi tengah-bawah di area aman Shorts.
- File ASS dan SRT disimpan terpisah.

### 7.8 Footage ⚙️
- Untuk setiap segmen, Haiku membuat 2–3 query bahasa Inggris dari teks segmen dan saran visual.
- Pexels Video Search mencari; saring berdasarkan durasi (≥ durasi segmen), resolusi, dan orientasi (portrait diutamakan; landscape diizinkan dengan crop 9:16).
- Simpan 3–5 kandidat per segmen.
- **Pemilihan otomatis:** Haiku (vision) menilai thumbnail setiap kandidat terhadap teks segmen dengan skor relevansi 0–10 dan alasan singkat. Kandidat dengan skor tertinggi di atas ambang (default 7) dipilih.
- Hindari memakai klip yang sama dua kali dalam satu video dan klip yang dipakai di video lain dalam jangka waktu konfigurasi.
- Jika tidak ada kandidat di atas ambang, coba query alternatif. Jika tetap gagal, pakai kandidat terbaik dan beri tanda `weak_visual` yang ditampilkan saat review akhir.
- Unduh ke file sementara (batas ukuran & timeout), validasi dengan FFprobe, hash SHA-256, lalu pindahkan ke folder pekerjaan.
- Metadata wajib: provider, ID aset, URL halaman, URL unduhan, nama & URL kontributor, dimensi, durasi, ukuran, hash, waktu pengambilan, teks atribusi, skor relevansi.
- Ketentuan Pexels (lisensi, atribusi, batas rate) wajib dicek dari dokumentasi resmi saat implementasi dan dicatat di `docs/decisions/`.

### 7.9 Render ⚙️
- Profil output: 1080×1920, 9:16, H.264, AAC 48 kHz, 30 fps, MP4, `+faststart`.
- Setiap segmen: footage di-scale dan di-crop ke 9:16, dipotong sesuai durasi segmen dari alignment.
- Overlay MVP: subtitle ASS dibakar; label seri kecil di awal (opsional).
- **Preview:** `h264_nvenc`. **Final:** `libx264`.
- Aturan FFmpeg:
  - `subprocess.run` dengan daftar argumen; **tidak pernah** `shell=True`.
  - `cwd` = folder pekerjaan dengan path relatif (menghindari escaping path Windows di filter `subtitles`/`ass`).
  - `fontsdir` ke `assets/fonts/`.
  - Tulis ke file sementara; pindahkan ke nama final hanya jika sukses dan lolos FFprobe.
  - Simpan stderr FFmpeg saat gagal.
  - Jangan menimpa output valid tanpa opsi eksplisit.

### 7.10 Quality gate ⚙️
Wajib lolos:
- File final ada, terbaca FFprobe, decode penuh tanpa error.
- 1080×1920, H.264/AAC, fps sesuai, durasi dalam rentang target.
- Audio ada, loudness dalam toleransi, tidak clipping.
- Subtitle tidak kosong; persentase `timing_uncertain` di bawah ambang.
- Semua segmen punya aset dengan metadata & atribusi lengkap.
- Semua `claim_ids` di naskah berstatus `verified`.
- Tidak ada file sementara tertinggal.

Jika gagal, sistem mencoba memperbaiki tahap terkait (maks. percobaan per tahap). Pengguna hanya diberi tahu jika perbaikan otomatis gagal, dengan bahasa sederhana.

### 7.11 Review akhir 👤
- Tugas Hari Ini menampilkan video final dengan pemutar besar dan **tiga pertanyaan singkat** (ya/tidak):
  1. Apakah gambarnya cocok dengan yang diucapkan?
  2. Apakah ada yang terdengar aneh atau salah?
  3. Apakah kamu bangga mengunggah ini?
- Tanda `weak_visual` atau `timing_uncertain` (jika ada) ditampilkan di bagian video yang relevan.
- Pilihan:
  - **Setuju** → paket ekspor dibuat.
  - **Perbaiki** → pengguna memilih satu alasan sederhana (gambar tidak cocok di detik tertentu / subtitle salah / suara perlu direkam ulang / isinya terasa salah). Sistem menerjemahkannya menjadi tindakan otomatis (ganti footage segmen itu, perbaiki subtitle, minta rekam ulang, atau buang topik).
  - **Buang** → topik dibuang.
- Persetujuan terikat pada **ID pekerjaan + versi naskah + hash video final**. Setiap perubahan artefak setelah persetujuan membatalkannya.

### 7.12 Paket ekspor dan upload 👤
Sistem membuat `data/jobs/<id>/export/`:
- `final.mp4`
- `upload.md`: satu judul yang direkomendasikan (+2 alternatif), deskripsi (ringkasan, daftar sumber, kredit Pexels), hashtag, label seri dan nomor episode
- `metadata.json`: ID pekerjaan, versi, hash, sumber, aset, biaya

Tugas Hari Ini menampilkan langkah upload dengan tombol salin untuk setiap bagian. 👤 Setelah upload, pengguna menempelkan URL video YouTube; status menjadi `published`.

---

## 8. State machine pekerjaan

```text
topic_queued
  → researching ⚙️
  → verifying ⚙️
  → scripting ⚙️
  → script_check ⚙️
  → awaiting_voice 👤
  → processing_audio ⚙️
  → aligning ⚙️
  → selecting_footage ⚙️
  → rendering ⚙️
  → quality_check ⚙️
  → pending_review 👤
  → approved
  → exported
  → published 👤 (URL dicatat)
```

Status alternatif: `dropped` (dengan alasan, mis. `insufficient_evidence`, `no_footage`, `script_check_failed`, `user_discarded`), `changes_requested`, `failed`, `cancelled`.

Aturan:
- Semua transisi didefinisikan di satu tempat dan divalidasi.
- Setiap transisi dicatat di `job_events` (dari, ke, waktu, alasan, aktor `system`/`user`).
- `failed` menyimpan tahap terakhir yang valid; `retry` melanjutkan dari sana.
- `changes_requested` menyimpan tindakan otomatis yang dipicu dan tahap tujuan.
- `dropped` bersifat normal, bukan error; sistem otomatis mengambil topik berikutnya.
- Artefak valid tidak dibuat ulang tanpa alasan (idempotent, berbasis hash input).
- Selesai hanya jika status database dan artefak sama-sama valid.

---

## 9. Model data (SQLite)

| Tabel | Isi utama |
|---|---|
| `topics` | id, teks, kategori, series, skor, alasan skor, footage_check (JSON), status (`candidate`/`queued`/`used`/`skipped`/`rejected`), alasan |
| `jobs` | id, topic_id, content_language, series, episode_number, status, last_valid_state, config snapshot, timestamps, error |
| `job_events` | job_id, from_state, to_state, actor, reason, created_at |
| `sources` | job_id, url, domain, publisher, title, tier, accessed_at, extracted_text_path |
| `claims` | job_id, source_ids (JSON), text, supporting_quote, quote_match_score, support_check, is_hook, status, reject_reason |
| `scripts` | job_id, version, hook, segments (JSON), ending, structure, check_results (JSON), status |
| `assets` | provider, provider_asset_id, page_url, download_url, contributor, contributor_url, width, height, duration, size, sha256, attribution, fetched_at |
| `segment_assets` | job_id, script_version, segment_index, asset_id, start_s, end_s, relevance_score, relevance_reason, flags |
| `artifacts` | job_id, kind, path, version, sha256, valid, created_at |
| `approvals` | job_id, script_version, final_sha256, decision, answers (JSON), reason, created_at, invalidated_at |
| `publications` | job_id, youtube_url, published_at (diisi manual) |
| `cost_entries` | job_id (nullable untuk biaya antrean topik), provider, model, operation, input_units, output_units, cost_usd, cost_kind (`actual`/`estimated`), created_at |

- Indeks pada `jobs.status`, `jobs.updated_at`, `topics.status`, `job_events.job_id`, `cost_entries.created_at`.
- Perubahan status dan persetujuan memakai transaksi.
- `shorts backup` memakai SQLite backup API.
- Tidak ada secret di database.

---

## 10. API backend (ringkas)

Prefix `/api`. Hanya mendengarkan di `127.0.0.1`. Tanpa autentikasi.

| Metode & path | Fungsi |
|---|---|
| `GET /health`, `GET /doctor` | Status & pemeriksaan lingkungan |
| `GET /tasks/today` | Daftar tugas pengguna saat ini (rekam, review, upload) |
| `GET /topics` · `POST /topics/{id}/skip` | Antrean topik · lewati |
| `POST /topics/refill` | Isi ulang antrean |
| `GET /jobs` · `GET /jobs/{id}` | Daftar & detail pekerjaan |
| `GET /jobs/{id}/events` | Stream progres (SSE) |
| `POST /jobs/{id}/retry` · `/cancel` | Lanjutkan · batalkan |
| `GET /jobs/{id}/teleprompter` | Naskah dalam format teleprompter |
| `POST /jobs/{id}/voice` | Unggah rekaman |
| `GET /jobs/{id}/review` | Data review akhir (video, tanda, pertanyaan) |
| `POST /jobs/{id}/approve` · `/fix` · `/discard` | Keputusan review |
| `GET /jobs/{id}/export` | Paket ekspor |
| `POST /jobs/{id}/published` | Catat URL YouTube |
| `GET /media/{job_id}/{path}` | File media (path divalidasi) |
| `GET /costs/summary` | Ringkasan biaya |

Pekerjaan berat dijalankan worker, bukan di dalam request HTTP.

---

## 11. CLI

```text
shorts init
shorts doctor
shorts serve
shorts today                       # tugas pengguna saat ini
shorts topics [--refill]
shorts produce [--count N]         # siapkan N naskah siap rekam (otomatis sampai awaiting_voice)
shorts voice <job_id> <file>
shorts review <job_id>             # buka video & tampilkan pertanyaan
shorts approve <job_id>
shorts fix <job_id> --reason footage|subtitle|voice|content [--at SECONDS]
shorts discard <job_id>
shorts export <job_id>
shorts published <job_id> <youtube_url>
shorts list [--status ...]
shorts status <job_id>
shorts retry <job_id>
shorts cancel <job_id>
shorts costs
shorts backup
```

Semua perintah yang memanggil layanan berbayar mendukung `--dry-run` (provider palsu, tanpa biaya).

`shorts doctor` memeriksa: Python, dependency, FFmpeg/FFprobe, `libass`, `h264_nvenc`, GPU/CUDA/VRAM, model Whisper, akses folder, variabel `.env` wajib, migrasi database, dan (dengan flag) koneksi API.

---

## 12. Frontend (repo privat `shorts-engine-ui`)

Dasar: Tailwick **React + TypeScript (Vite)**.

Aturan:
- Pakai komponen, layout, dan token warna Tailwick; jangan menambah library UI lain tanpa alasan tercatat.
- Hapus halaman demo yang tidak dipakai. Dark mode aktif.
- Teks UI dalam Bahasa Indonesia; konten video (naskah, subtitle, judul) dalam bahasa konten.
- Satu pustaka data-fetching (mis. TanStack Query) dan klien API yang diketik dari skema backend.
- **Prinsip UX:** setiap layar hanya punya satu tindakan utama yang jelas. Tidak ada istilah teknis di layar pengguna.

Halaman (urut prioritas):
1. **Tugas Hari Ini** ⭐ — halaman utama. Kartu bertahap: "Rekam 3 naskah" → "Tonton 2 video" → "Upload 1 video". Setiap kartu membuka alurnya sendiri.
2. **Teleprompter & unggah rekaman** — naskah besar, catatan pelafalan, tombol unggah, umpan balik jika rekaman tidak cocok.
3. **Review akhir** — pemutar video, tiga pertanyaan, tombol Setuju / Perbaiki / Buang.
4. **Upload** — judul, deskripsi, hashtag dengan tombol salin; kolom URL YouTube.
5. **Dapur produksi** (untuk yang penasaran, tidak wajib) — antrean topik, pipeline kanban, detail pekerjaan, sumber dan klaim.
6. **Pengaturan** — hasil `doctor`, status API key (ada/tidak), anggaran, bahasa konten.

---

## 13. Konfigurasi dan secret

`.env` (tidak masuk Git):

```text
ANTHROPIC_API_KEY=
TAVILY_API_KEY=
PEXELS_API_KEY=
EXA_API_KEY=                 # opsional
DATA_DIR=./data
FRONTEND_DIST_DIR=           # opsional
LOG_LEVEL=INFO
```

`config/settings.yaml` (nonrahasia), antara lain:
- `content_language` (default `en`).
- Model LLM per tugas dan harga per juta token.
- Ambang verifikasi (kemiripan kutipan, jumlah klaim minimum, sumber hook minimum).
- Ukuran antrean topik minimum, ambang footage minimum per topik.
- Target durasi dan kecepatan bicara per bahasa.
- Ambang relevansi footage dan masa tunggu pemakaian ulang klip.
- Profil render, gaya subtitle, model Whisper per rentang VRAM.
- Anggaran dan kurs `USD_IDR`.
- Batas unduhan.

`config/topic_policy.yaml`: kategori aktif, seri dan struktur ceritanya, daftar topik terlarang.

`config/style_guide.md`: persona dan gaya naskah (lihat file terpisah).

Keamanan:
- Secret hanya dari environment; tidak pernah di kode, log, database, pesan error, atau respons API.
- Tidak pernah mengeksekusi kode atau perintah yang berasal dari keluaran LLM.
- Validasi semua path; media hanya disajikan dari dalam folder pekerjaan.
- Unduhan memakai file sementara, batas ukuran, timeout, dan validasi isi.

---

## 14. Kontrol biaya

| Batas | Default |
|---|---|
| Per video | Rp5.000 |
| Per bulan | Rp100.000 |
| Peringatan | 80% batas bulanan |
| Maksimum percobaan per tahap | 3 |

- Token aktual dari respons API dicatat sebagai `actual`; yang tidak tersedia sebagai `estimated`.
- Biaya antrean topik dan topik yang dibuang tetap dicatat dan dihitung dalam anggaran bulanan.
- Sebelum tahap berbayar, sistem memeriksa anggaran; jika terlampaui, tahap tidak dijalankan dan pengguna diberi tahu di Tugas Hari Ini.
- Pemakaian kuota gratis Tavily/Pexels dicatat sebagai jumlah panggilan.
- Laporan biaya menampilkan rasio topik dibuang vs dipakai, agar ambang verifikasi bisa disetel.

---

## 15. Keandalan dan error

- Timeout wajib untuk semua HTTP dan subprocess.
- Retry terbatas dengan exponential backoff + jitter hanya untuk error sementara (timeout, 429, 5xx). Hormati `Retry-After`.
- Tidak ada retry untuk error autentikasi, input tidak valid, atau anggaran habis.
- Pesan error teknis menyertakan ID pekerjaan dan tahap; pesan untuk pengguna memakai bahasa sederhana.
- Cache respons search dan Pexels per query jika diizinkan ketentuan provider.
- Pembersihan file sementara saat startup dan setelah tahap selesai.
- Saat startup, pekerjaan yang terhenti di tengah tahap dikembalikan ke `failed` dengan alasan `interrupted`.

---

## 16. Pengujian

Tes default **tanpa jaringan dan tanpa API berbayar**. Provider palsu dan fixture untuk semua tes otomatis. Tes API sungguhan diberi marker `live`.

Unit:
- Konfigurasi dan skema.
- Semua transisi state machine (legal dan ilegal).
- Setiap aturan verifikasi klaim (kutipan tidak ditemukan, tier C, angka berbeda, hook dengan satu sumber).
- Pemeriksaan naskah (angka/nama tanpa klaim, kalimat tanpa `claim_id`, kata berlebihan).
- Filter topik (terlarang, duplikat, footage kurang).
- Pemeriksaan kecocokan rekaman vs naskah.
- Alignment naskah ↔ timestamp Whisper.
- Pembuatan ASS (pemecahan baris, highlight per kata).
- Pemilihan footage (ambang, larangan pakai ulang).
- Penerjemahan alasan "Perbaiki" menjadi tindakan.
- Biaya dan anggaran.
- Pembatalan persetujuan saat artefak berubah.
- Pembentukan argumen FFmpeg.

Integrasi:
- Pipeline lengkap dengan provider palsu dan fixture media pendek.
- Render nyata pada fixture, diverifikasi FFprobe.
- Resume setelah kegagalan.
- Topik dibuang otomatis dan sistem mengambil topik berikutnya.
- Endpoint API utama dengan `TestClient`.

Manual (dicatat di laporan fase):
- Kualitas naskah bahasa Inggris dan kesesuaian dengan persona.
- Akurasi timing subtitle pada rekaman sungguhan.
- Keterbacaan subtitle di layar ponsel.
- Relevansi footage pilihan otomatis.
- Loudness dan tidak ada clipping.
- Kredit aset di `upload.md`.
- Waktu kerja pengguna per video.

---

## 17. Fase implementasi

Satu fase dikerjakan sampai tes lulus, di-commit, dan laporan fase ditulis, sebelum fase berikutnya.

### Fase 1 — Fondasi backend
- Struktur repo, `uv`, konfigurasi, logging dengan rotasi.
- SQLAlchemy + Alembic, semua tabel di bagian 9.
- State machine + `job_events`.
- CLI: `init`, `doctor`, `list`, `status`, `cancel`, `backup`.
- `doctor` mendeteksi GPU/VRAM/NVENC/libass.
- Interface provider + provider palsu.
- Unit test.

**Selesai jika:** pekerjaan bisa dibuat dan statusnya dipindah secara valid; `doctor` akurat di mesin pengguna; tes lulus.

### Fase 2 — Topik, riset, verifikasi, naskah
- Provider Anthropic (Sonnet/Haiku) dengan output terstruktur tervalidasi.
- Provider Tavily + tier sumber.
- Antrean topik otomatis dengan filter kebijakan, duplikat, dan cek footage Pexels.
- Verifikasi klaim otomatis (bagian 7.3).
- Naskah + pemeriksaan naskah otomatis (bagian 7.4).
- `shorts produce --count N` sampai `awaiting_voice`.
- Pencatatan biaya dan anggaran.
- Laporan kualitas: untuk 10 topik, tampilkan naskah, klaim, sumber, dan alasan topik dibuang, agar pengguna bisa menilai sekali secara keseluruhan.

**Selesai jika:** `shorts produce --count 3` menghasilkan 3 naskah siap rekam yang hanya memakai klaim `verified`, dengan topik yang gagal dibuang otomatis; biaya tercatat.

### Fase 3 — Media dan video pertama
- Unggah dan pemrosesan suara + pemeriksaan kecocokan rekaman.
- faster-whisper + alignment + subtitle ASS highlight per kata.
- Provider Pexels, pemilihan otomatis dengan vision, unduhan aman, metadata lengkap.
- Render preview dan final.
- Quality gate otomatis dengan perbaikan otomatis.
- Review akhir lewat CLI (`approve`/`fix`/`discard`), pembatalan persetujuan.
- Paket ekspor dan `published`.

**Selesai jika:** pengguna dapat menghasilkan video final yang lolos quality gate hanya dengan merekam, menonton, dan menyetujui lewat CLI.

### ⏸️ Titik validasi
Sebelum Fase 4, pengguna memproduksi dan mengunggah **10 video** lewat CLI, mencoba ketiga seri. Catat waktu kerja per video, berapa video yang perlu "Perbaiki", dan alasannya. Hasilnya menentukan penyetelan ambang dan prioritas UI.

### Fase 4 — Web UI: Tugas Hari Ini
- API FastAPI (bagian 10) + SSE + worker.
- Setup `shorts-engine-ui` dari Tailwick React TS.
- Halaman 1–4 di bagian 12 (Tugas Hari Ini, Teleprompter, Review akhir, Upload) dan Pengaturan.
- `shorts serve` menyajikan API + build frontend.

### Fase 5 — Dapur produksi dan penyempurnaan
- Halaman Dapur produksi.
- Penyetelan ambang dari pengaturan.
- Perbaikan berdasarkan temuan titik validasi.

### Fase lanjutan (setelah MVP)
- Channel kedua Bahasa Indonesia (riset dipakai ulang; naskah dan rekaman per bahasa).
- Rekam langsung di browser.
- Visual programatik → membuka kategori luar angkasa, fisika, teknologi.
- Upload YouTube via OAuth (private/unlisted, pencegahan duplikat, dry-run).
- Integrasi Shorts series resmi setelah channel masuk YouTube Partner Program.
- Analitik dan laporan mingguan.
- Provider cadangan (Exa).

---

## 18. Kriteria penerimaan MVP (akhir Fase 3)

1. Sistem mengisi antrean topik sendiri dan membuang topik yang tidak lolos tanpa melibatkan pengguna.
2. Tidak ada secret di kode, log, database, atau Git.
3. Hanya klaim yang lolos semua aturan verifikasi otomatis yang dipakai naskah; alasan penolakan tercatat.
4. Naskah terstruktur, setiap kalimat faktual terhubung ke klaim, dan lolos pemeriksaan naskah.
5. Rekaman yang tidak cocok dengan naskah terdeteksi dan diminta rekam ulang.
6. Subtitle memakai teks naskah dengan timing per kata dari Whisper.
7. Footage dipilih otomatis dari beberapa kandidat; metadata, atribusi, dan skor relevansi tercatat.
8. MP4 1080×1920 valid dan lolos quality gate.
9. Persetujuan wajib, terikat versi dan hash, dan batal otomatis saat ada perubahan.
10. "Perbaiki" menghasilkan tindakan otomatis yang benar.
11. Paket ekspor berisi judul, deskripsi, sumber, kredit, seri, dan nomor episode.
12. Pekerjaan dapat dilanjutkan setelah gagal tanpa merusak artefak valid.
13. Biaya tercatat dan anggaran ditegakkan.
14. README menjelaskan instalasi di Windows hingga video pertama.
15. Semua tes otomatis lulus tanpa API berbayar.
16. Waktu kerja pengguna per video terukur, target ≤ 20 menit.

MVP **tidak** dianggap selesai hanya karena satu MP4 berhasil dibuat.

---

## 19. Format laporan per fase

`docs/phase-reports/phase-N.md`:

1. Fitur yang selesai (hanya yang benar-benar berfungsi).
2. File yang dibuat/diubah.
3. Keputusan arsitektur (tautkan ADR).
4. Perintah instalasi dan menjalankan.
5. Tes dan hasilnya.
6. Masalah yang belum selesai.
7. Konfigurasi/API key yang perlu disediakan pengguna.
8. Estimasi biaya yang belum pasti.
9. Kriteria penerimaan yang sudah dan belum terpenuhi.
10. Langkah konkret fase berikutnya.

---

## 20. Pertanyaan terbuka

- **Nama channel.** Kandidat ada di `style_guide.md`; wajib cek ketersediaan di YouTube, Instagram, dan TikTok.
- **Font subtitle.** AI agent mengusulkan 2–3 font berlisensi bebas di Fase 3; pengguna memilih satu.
- **Musik latar.** Ditunda.

---

## 21. Referensi resmi

Selalu cek versi terbaru saat implementasi dan catat keputusan yang terpengaruh di `docs/decisions/`.

- Claude API: https://docs.claude.com/en/api/overview
- Tavily: https://docs.tavily.com
- Pexels API: https://www.pexels.com/api/documentation/
- Lisensi Pexels: https://www.pexels.com/license/
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- FFmpeg: https://ffmpeg.org/documentation.html
- FastAPI: https://fastapi.tiangolo.com
- Kebijakan monetisasi YouTube: https://support.google.com/youtube/answer/1311392
- Lisensi Envato Elements: https://help.elements.envato.com/hc/en-us/articles/360000628966-Envato-Elements-License

---

**Definisi keberhasilan:** pengguna cukup merekam, menonton sekali, dan mengunggah; sistem menghasilkan video edukasi yang valid secara teknis, faktanya dapat diaudit, asetnya tercatat, dan hanya diekspor setelah persetujuan yang sah. Performa channel dan monetisasi dievaluasi terpisah dari hasil nyata.
