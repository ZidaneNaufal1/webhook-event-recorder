# Changelog

## 1.0.0 — 2026-10-25

- Receiver localhost POST /events dengan HMAC-SHA256 mengikat timestamp, ID dan byte payload.
- Validasi header/payload, jendela timestamp, batas 64 KiB dan schema JSON eksplisit.
- Penyimpanan SQLite transaksional: baru 201, duplikat identik 200, konflik 409.
- CLI metadata dan replay localhost dengan signature baru; demo sintetis lintas dua database.
- Tes perilaku, CI Windows/Linux, dokumentasi Indonesia + English summary, MIT dan provenance.
