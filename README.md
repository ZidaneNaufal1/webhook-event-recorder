# Webhook Event Recorder

[![Tes](https://github.com/ZidaneNaufal1/webhook-event-recorder/actions/workflows/tests.yml/badge.svg)](https://github.com/ZidaneNaufal1/webhook-event-recorder/actions/workflows/tests.yml)

Receiver webhook **lokal** untuk belajar dan menguji integrasi: verifikasi HMAC-SHA256, simpan event ke SQLite, cegah penyimpanan ulang, dan replay ke receiver localhost lain. Python 3.10–3.14, standard library saja, lisensi MIT.

Proyek ini memakai protokol sendiri. Belum merupakan adapter GitHub/Stripe atau layanan webhook produksi.

## Coba langsung

```console
git clone https://github.com/ZidaneNaufal1/webhook-event-recorder.git
cd webhook-event-recorder
python examples/run_demo.py
python -m unittest -v
```

Di Windows, gunakan `py -3` bila command `python` belum tersedia. Tidak perlu pip. Demo membuat secret acak dalam memori, dua server loopback secara bergantian, dan database sementara yang dibersihkan setelah selesai. Tidak menghubungi layanan internet.

```text
Demo lulus: simpan 201, duplikat 200, konflik 409, HMAC salah 401.
Replay CLI ke database lokal kedua: 201 lalu 200; payload identik dan sumber utuh.
Secret sementara tidak dicetak atau disimpan; tidak ada layanan eksternal dihubungi.
```

## Jalankan receiver sendiri

Contoh PowerShell: buat secret lokal baru tanpa mencetak nilainya, mulai receiver sebagai proses latar pada terminal yang sama, lalu kirim payload sintetis:

```powershell
$env:WEBHOOK_SECRET = python -c "import secrets; print(secrets.token_hex(32))"
$receiver = Start-Process -FilePath python -ArgumentList 'recorder.py','serve','--db','events.db','--port','8766' -WindowStyle Hidden -PassThru
python examples/send_demo.py --port 8766
python examples/send_demo.py --port 8766
python recorder.py list --db events.db --limit 10
```

Tunggu sampai receiver siap sebelum menjalankan pengirim; jika koneksi belum tersedia, ulangi pengirim. Hasil pertama `HTTP 201`, pengiriman identik berikutnya `HTTP 200`; hanya satu baris tersimpan. Proses anak mewarisi secret dari environment. Hentikan proses contoh setelah selesai:

```powershell
Stop-Process -Id $receiver.Id
Remove-Item Env:WEBHOOK_SECRET
```

Untuk melihat log startup dan berhenti dengan Ctrl+C, jalankan `python recorder.py serve --db events.db --port 8766` langsung pada terminal yang sudah memiliki secret. Terminal pengirim lain harus menerima secret yang sama melalui mekanisme lokal yang dipercaya; jangan menaruh nilainya di riwayat command atau issue.

Alternatif Bash (dua terminal tidak diperlukan):

```bash
export WEBHOOK_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python recorder.py serve --db events.db --port 8766 &
receiver_pid=$!
python examples/send_demo.py --port 8766
python recorder.py list --db events.db
kill "$receiver_pid"
unset WEBHOOK_SECRET
```

Secret wajib 32–4096 byte UTF-8; contoh menghasilkan 32 byte acak sebagai 64 karakter hex. Default nama environment `WEBHOOK_SECRET`, dapat diganti dengan `--secret-env NAMA`. Tidak ada flag yang menerima nilai secret di command line. Server hanya bind `127.0.0.1`; CLI tidak menyediakan opsi host publik.

## Protokol request

`POST /events` dengan satu header masing-masing:

| Header | Isi |
| --- | --- |
| Content-Type | `application/json` atau `application/json; charset=utf-8` |
| Content-Length | Panjang byte body, 1–65536 |
| X-Webhook-Id | 1–64 huruf ASCII, angka, `_` atau `-` |
| X-Webhook-Timestamp | Detik Unix desimal, timestamp dalam ±300 detik dari jam receiver |
| X-Webhook-Signature | `sha256=` diikuti 64 karakter hex lowercase |

Payload UTF-8 berbentuk object dengan **hanya** `type` dan `data`:

```json
{"type":"study_completed","data":{"minutes":25}}
```

`type` mengikuti format nama ID; `data` wajib object. Field/kunci berulang, JSON terlalu dalam, literal NaN/Infinity, integer di atas 128 digit, angka floating yang menjadi non-finite, UTF-8 rusak dan schema lain ditolak. Payload tepat 64 KiB diuji; ukuran lebih besar ditolak sebelum dibaca. Chunked/Content-Encoding tidak diproses; gunakan body dengan Content-Length jelas.

Cara membuat signature:

```python
message = timestamp.encode("ascii") + b"." + event_id.encode("ascii") + b"." + raw_body
signature = "sha256=" + hmac.new(secret, message, hashlib.sha256).hexdigest()
```

Tanda tangan mencakup **timestamp + ID + byte body asli** dan diperiksa memakai `hmac.compare_digest`. Jangan serialisasi ulang JSON setelah menandatangani. HMAC memverifikasi kepemilikan secret; tidak mengenkripsi transport atau payload.

## Status dan deduplikasi

| HTTP | Arti |
| --- | --- |
| 201 | Event baru tersimpan |
| 200 | ID dan byte payload sama; tidak menambah baris |
| 409 | ID sama, byte payload berbeda; data awal dipertahankan |
| 401 | Signature/ID/timestamp tidak valid atau timestamp di luar jendela |
| 400 | Header/framing/payload tidak sesuai |
| 404 / 405 / 415 | Route / metode / media type tidak didukung |
| 408 / 413 / 503 | Timeout membaca body / body terlalu besar / storage tidak tersedia |

Respons aplikasi berupa JSON kecil dengan field `result`. Kesalahan parsing HTTP dasar dapat ditangani oleh server standard library. Request framing yang tidak valid atau client yang terus mengirim body berlebihan dapat berakhir sebagai koneksi terputus, bukan respons HTTP lengkap.

Deduplikasi bersifat global per database dan membandingkan **byte**, sehingga JSON yang hanya berbeda whitespace tetap konflik untuk ID sama. Seluruh request, termasuk duplikat, tetap wajib memiliki autentikasi dan timestamp yang valid. Tidak ada efek bisnis yang dieksekusi; jaminan ini hanya tentang penyimpanan baris, bukan exactly-once processing pada sistem lain.

## Metadata dan replay

```console
python recorder.py list --db events.db --limit 20
python recorder.py replay --db events.db --event-id demo-001 --port 8767
```

Mulai receiver kedua pada port 8767 dengan database berbeda serta secret yang sama sebelum replay. Replay membaca byte payload asli, mempertahankan ID, lalu menandatangani ulang dengan timestamp sekarang. Database sumber tidak diubah. Replay ke database yang sudah memiliki event identik menghasilkan 200. Tujuan selalu `http://127.0.0.1:PORT/events`; tidak ada URL bebas, proxy environment atau redirect otomatis.

`list` menampilkan ID, waktu penerimaan UTC, timestamp asli, type dan jumlah byte; tidak mencetak body. Limit 1–100, terbaru menurut urutan insert. Replay mencetak ID/status saja. Exit CLI 0 sukses (replay 200/201), 1 receiver menolak replay, 2 argumen/config/storage/koneksi gagal, 130 Ctrl+C saat receiver aktif.

## Batas dan data lokal

- **Payload asli tersimpan di SQLite tanpa enkripsi.** HMAC secret dan signature request tidak disimpan, tetapi data sensitif dapat berada di payload, ID atau type. Gunakan data sintetis; jangan unggah database, secret, `.env` atau payload privat. `.gitignore` sudah mengabaikan database dan file `private-*`.
- Server development single-thread, request socket timeout 2 detik; pengirim lokal timeout socket 3 detik. Timeout bukan deadline keras seluruh koneksi. Tidak ada TLS, rate limiting, retensi otomatis, multitenancy, key rotation atau proteksi DoS produksi. Localhost tetap dapat diakses proses lain pada komputer. Jangan expose melalui tunnel/reverse proxy publik.
- Database milik sendiri diperlukan. Schema/application_id diverifikasi; file asing/rusak, symlink/reparse pada path stabil ditolak. Bukan sandbox terhadap database berbahaya, hardlink atau race filesystem. Backup/izin file adalah tanggung jawab operator.
- Satu transaksi dengan `BEGIN IMMEDIATE`, pengecekan ID, dan insert menjaga deduplikasi. Data baru diakui setelah commit; proses yang mati setelah commit tetapi sebelum respons dapat dikirim ulang secara aman dengan ID dan byte sama.
- Jendela timestamp membantu membatasi request lama; tidak menghentikan pemegang secret mengirim event baru. Jam lokal harus benar. Replay memang membuat signature baru; tidak boleh digunakan untuk mengirim data ke proses lokal yang belum ditinjau operator.

## Pengembangan

CI menjalankan `python -m unittest -v` dan `python examples/run_demo.py` pada Ubuntu/Windows × Python 3.10, 3.12, 3.14, tanpa layanan eksternal. Tes symlink dapat dilewati jika izin OS tidak tersedia.

Lihat [CONTRIBUTING](CONTRIBUTING.md), [keputusan dan bukti](docs/DECISIONS.md), [changelog](CHANGELOG.md), dan [asal kode](PROVENANCE.md). Rencana berikutnya: batas waktu total request dan dokumentasi rotasi key lokal, belum diimplementasikan.

Owner/maintainer: **Muhammad Zidane Naufal Azzam ([ZidaneNaufal1](https://github.com/ZidaneNaufal1))**. Implementasi, pengujian dan dokumentasi dibantu Ciel/OpenAI Codex. Belum ada klaim audit keamanan independen, adopsi eksternal atau pemahaman maintainer yang sudah diverifikasi.

## English summary

A local-only webhook recorder using HMAC-SHA256 over timestamp, event ID and raw JSON bytes. SQLite deduplicates identical deliveries and rejects conflicting IDs. The CLI lists metadata and replays stored events to an explicitly selected localhost port using fresh signatures. The original payload is stored unencrypted; this single-thread HTTP development server is not a production endpoint or provider-specific adapter. Python standard library only; MIT; developed with Ciel/OpenAI Codex assistance.
