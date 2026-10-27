# CLAUDE.md — Shorts Engine

Panduan kerja untuk AI coding agent di repo ini. Baca file ini dan `SPEC.md` sebelum mulai bekerja.

## Apa proyek ini

Aplikasi lokal Windows yang memproduksi YouTube Shorts sains (konten berbahasa Inggris, bahasa dapat dikonfigurasi) dalam mode **dibimbing sistem**: sistem memilih topik, meriset, memverifikasi fakta secara otomatis dengan aturan ketat, menulis naskah, memilih footage, membuat subtitle, merender, dan memeriksa kualitas. Pengguna hanya merekam narasi, menonton hasil sekali lalu menyetujui, dan mengunggah.

Konsekuensi desain: **jangan pernah membuat fitur yang memaksa pengguna memutuskan hal teknis atau isi.** Jika sistem ragu, sistem membuang klaim atau topik dan melanjutkan, bukan bertanya ke pengguna.

Spesifikasi lengkap: `SPEC.md`. Jika ada konflik antara file ini dan `SPEC.md`, ikuti `SPEC.md` lalu laporkan konfliknya.

## Lingkungan

- Windows 10/11, Intel i5, RAM 16 GB, NVIDIA RTX 3050 6 GB.
- Python 3.12 via `uv`. FFmpeg/FFprobe dengan `libass`.
- Shell pengguna: PowerShell. Tulis perintah di dokumentasi untuk PowerShell.

## Perintah umum

```powershell
uv sync                          # pasang dependency
uv run shorts doctor             # periksa lingkungan
uv run pytest                    # tes (tanpa jaringan, tanpa API berbayar)
uv run pytest -m live            # tes API sungguhan, HANYA jika diminta pengguna
uv run alembic upgrade head      # migrasi database
uv run shorts serve              # API + UI di 127.0.0.1
```

## Cara bekerja

1. **Satu fase dalam satu waktu** sesuai bagian 17 `SPEC.md`. Jangan mengerjakan fitur dari fase berikutnya.
2. Mulai setiap sesi dengan membaca laporan fase terakhir di `docs/phase-reports/`.
3. Kerjakan dalam langkah kecil; jalankan tes setelah setiap perubahan berarti.
4. Fase selesai hanya jika: tes lulus, kriteria fase terpenuhi, laporan `docs/phase-reports/phase-N.md` ditulis, dan perubahan di-commit.
5. **Jangan pernah mengklaim fitur selesai jika masih stub, mock, atau placeholder.** Sebutkan statusnya apa adanya.
6. Keputusan teknis yang berarti dicatat sebagai ADR singkat di `docs/decisions/NNNN-judul.md`.
7. Tanya pengguna sebelum: menambah dependency baru yang besar, mengubah skema database yang sudah dimigrasi, atau menyimpang dari `SPEC.md`.
8. Jika butuh sesuatu yang hanya bisa disediakan pengguna (API key, file, hasil uji di mesinnya), berhenti dan minta dengan jelas.

## Aturan arsitektur

- Logika pipeline ada di `src/shorts_engine/pipeline/` dan `services/`; **tidak boleh bergantung pada FastAPI**. API dan CLI hanya memanggil service.
- Semua akses ke layanan luar lewat interface di `providers/` (llm, search, footage, asr). Setiap provider punya versi palsu untuk tes.
- Semua output LLM divalidasi dengan Pydantic. Output yang gagal validasi adalah error, bukan diterima diam-diam.
- Transisi status hanya lewat modul state machine; tidak ada update kolom `status` secara langsung.
- Perubahan status, persetujuan, dan pencatatan biaya memakai transaksi database.
- Tahap yang menghasilkan artefak harus idempotent (berbasis hash input).
- Simpan konfigurasi nonrahasia di `config/settings.yaml`; nama model dan harga tidak di-hardcode.

## Aturan FFmpeg dan Windows

- Selalu `subprocess.run([...])` dengan daftar argumen. **Dilarang** `shell=True` atau string perintah.
- Jalankan FFmpeg dengan `cwd` = folder pekerjaan dan path relatif, terutama untuk filter `subtitles`/`ass` (menghindari masalah escaping `C:\`).
- Pakai `fontsdir` ke `assets/fonts/`.
- Pakai `pathlib.Path` di semua tempat. Jangan merangkai path dengan string.
- Render ke file sementara; pindahkan ke nama final hanya setelah sukses dan lolos FFprobe.
- Simpan stderr FFmpeg ke log pekerjaan saat gagal.
- Set timeout untuk setiap subprocess.

## Aturan fakta dan konten

- **Jangan pernah mengarang** fakta ilmiah, URL, nama sumber, metadata lisensi, atau hasil API.
- URL sumber hanya boleh berasal dari respons search API, tidak pernah dari keluaran LLM.
- Setiap klaim wajib lolos semua aturan verifikasi otomatis di `SPEC.md` bagian 7.3. Jangan melonggarkan aturan ini untuk "membuat tes lulus" atau agar lebih banyak topik lolos; ubah ambang hanya lewat konfigurasi dan catat alasannya di ADR.
- Naskah hanya boleh memakai klaim berstatus `verified`; setiap kalimat faktual wajib merujuk `claim_id`.
- Wikipedia dan sumber tier C tidak pernah menjadi pendukung klaim.

## Larangan keras

- Jangan pernah mengunggah ke YouTube atau layanan publik apa pun.
- Jangan pernah commit `.env`, API key, token, atau isi folder `data/`.
- Jangan pernah menulis secret ke log, database, pesan error, atau respons API.
- Jangan pernah mengeksekusi kode atau perintah shell yang dihasilkan model.
- **Jangan pernah menyalin file template Tailwick ke repo ini.** Repo ini publik; frontend ada di repo privat `shorts-engine-ui`.
- Jangan menjalankan tes yang memanggil API berbayar kecuali pengguna memintanya.
- Jangan melakukan retry tanpa batas. Error autentikasi, input tidak valid, dan anggaran habis tidak di-retry.

## Konvensi kode

- Kode, nama variabel, komentar, docstring, commit message, dan dokumentasi teknis: **bahasa Inggris**.
- Teks UI dan pesan untuk pengguna: **Bahasa Indonesia**, bahasa sederhana tanpa istilah teknis.
- Konten video (naskah, subtitle, judul, deskripsi): mengikuti `content_language` di konfigurasi (default `en`). Jangan hardcode bahasa.
- Persona dan aturan naskah ada di `config/style_guide.md`; jangan menyalin isinya ke kode, baca dari file.
- Type hints wajib. Format dan lint dengan `ruff`.
- Fungsi kecil dan bisa diuji; hindari kelas "manager" raksasa.
- Error domain memakai exception khusus dengan ID pekerjaan dan nama tahap.

## Testing

- Tes default tanpa jaringan dan tanpa API berbayar; gunakan provider palsu dan fixture di `tests/fixtures/`.
- Fixture media harus kecil (audio ±5 detik, video ±3 detik).
- Tes API sungguhan diberi marker `@pytest.mark.live`.
- Setiap bug yang diperbaiki mendapat tes regresi.

## Frontend (repo terpisah)

Frontend ada di `shorts-engine-ui` (privat), berbasis Tailwick React + TypeScript (Vite). Saat bekerja di repo itu:
- Pakai komponen dan token warna Tailwick; jangan menambah library UI lain tanpa persetujuan.
- Hapus halaman demo yang tidak dipakai.
- Teks UI dalam Bahasa Indonesia.
- Tipe API diturunkan dari skema backend.
