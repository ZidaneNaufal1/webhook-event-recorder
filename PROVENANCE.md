# Asal kode dan data

Owner/maintainer: Muhammad Zidane Naufal Azzam (ZidaneNaufal1).

Kode, tes, fixture dan dokumentasi disusun untuk repository ini pada 2026-10-25 dengan bantuan Ciel/OpenAI Codex. Tidak diklaim seluruh kode ditulis manual atau sudah dipahami sepenuhnya oleh maintainer. Pola validasi path/database dikembangkan dari pendekatan pada repository milik owner, http-status-monitor (MIT); tidak ada dependency runtime antar-repository.

Runtime memakai Python standard library, termasuk hmac/hashlib, http, sqlite3, json, argparse dan utilitas bawaan. Semua event/secret tes sintetis; demo memakai secret acak dalam memori. Tidak ada payload produksi, dataset eksternal, credential tersimpan atau aset visual pihak ketiga. GitHub Actions checkout/setup-python digunakan untuk CI. Lisensi proyek MIT, ketentuan toolchain/layanan terpisah.
