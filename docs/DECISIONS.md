# Keputusan dan bukti

## Pengguna dan tujuan

Pengembang integrasi membutuhkan fixture webhook yang bisa dijalankan tanpa akun layanan luar. Versi awal menerima satu protokol lokal, menyimpan byte payload dan metadata, lalu mereproduksi pengiriman. Tidak memproses pembayaran, pekerjaan bisnis, atau callback eksternal.

## HMAC dan replay

HMAC mencakup timestamp, ID dan raw body. Menandatangani body saja memungkinkan ID diganti untuk melewati deduplikasi; tes mengubah masing-masing komponen dan memastikan 401. Timestamp ±300 detik membatasi request lama; replay CLI sengaja menandatangani ulang timestamp sekarang. Secret berasal dari environment dan tidak dipersistenkan. Fixture memakai secret sintetis atau secret acak sementara.

## Idempotensi dan transaksi

ID menjadi primary key SQLite; `BEGIN IMMEDIATE` memperoleh write lock sebelum membaca ID. Payload identik menghasilkan 200, isi berbeda menghasilkan 409, dan insert baru menghasilkan 201 setelah commit. Hanya penyimpanan yang idempotent; tidak ada klaim exactly-once untuk efek eksternal. Perbandingan byte menghindari perubahan payload asli, dengan konsekuensi whitespace berbeda dianggap konflik.

## Batas resource dan privasi

Header Content-Length diperiksa sebelum pembacaan body maksimal 64 KiB. Body kecil dengan framing jelas dibaca sebelum penolakan route/auth agar respons Windows tidak hilang akibat unread request data. JSON baru diparse setelah autentikasi. Tidak ada chunked/kompresi, database asing atau binding host publik. Single-thread dan socket timeout cocok untuk latihan lokal, bukan endpoint produksi atau deadline total.

Payload asli disimpan tanpa enkripsi agar replay mempertahankan byte. CLI metadata dan log aplikasi tidak mencetak body/secret. Operator tetap harus memakai data sintetis dan direktori sendiri; ini bukan jaminan menghilangkan informasi sensitif dari payload.

## Validasi lokal 2026-10-25

Windows, CPython 3.14.0: 30 tes dijalankan, 29 lulus dan 1 symlink dilewati karena izin sistem. `python examples/run_demo.py` lulus: 201 baru, 200 duplikat, 409 konflik dan 401 HMAC salah; replay CLI ke database kedua menghasilkan 201 lalu 200, payload identik dan database sumber tetap utuh.

Tes mencakup tampering ID/body/timestamp, timestamp lama/masa depan, JSON rusak/duplikat/deep/numerik berlebihan, ukuran tepat/lebih 64 KiB, header hilang/duplikat, body terpotong, database asing/versi schema, rollback insert dan metadata tanpa body. CI memverifikasi tes/demo pada enam kombinasi OS/versi Python dari checkout bersih; hasil tersedia di tab Actions pada commit/tag rilis.

Pengujian awal menemukan WinError 10053 saat route salah ditolak sebelum body kecil dibaca. Runtime diperbaiki membaca body berframing jelas dalam batas sebelum respons penolakan; fixture untuk framing tidak valid berhenti mengirim body yang memang sudah ditolak. Request berlebihan/malformed tetap dapat menerima connection close. Tes berikutnya dan demo lulus. Tidak ada benchmark beban, audit keamanan independen, pengguna eksternal atau sertifikasi yang diklaim.

## Panduan memahami kode

Ikuti `main → make_server → do_POST → signature/parse_event → store_event`. Jalur replay: `main → SELECT body → deliver → localhost /events`. Jelaskan mengapa ID ikut ditandatangani, mengapa byte asli disimpan, bagaimana transaksi mencegah insert ulang, dan mengapa HMAC tidak mengenkripsi data. Ini panduan belajar; pemahaman maintainer belum dinyatakan selesai.
