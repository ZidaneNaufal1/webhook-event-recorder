# Berkontribusi

Gunakan Python 3.10+ dan jalankan `python -m unittest -v` serta `python examples/run_demo.py` dari checkout bersih. Semua request tes harus memakai loopback dan data sintetis. Tidak ada dependency pip. Tes symlink dapat dilewati bila OS tidak mengizinkan.

Untuk bug, tulis OS/versi Python, versi tool, langkah lokal, status yang diharapkan, dan hasil aktual. Jangan unggah secret, header signature produksi, payload privat atau database asli ke issue publik. Reproduksi dengan body sintetis dan secret baru yang hanya dipakai pada tes.

Perubahan harus menjaga pengikatan ID/timestamp/body dalam HMAC, perbandingan digest, batas payload, autentikasi pada duplikat, transaksi penyimpanan dan replay localhost tanpa redirect. Tambahkan tes perilaku saat memperbaiki bug. Diskusikan perubahan protokol/schema sebelum implementasi agar kompatibilitas dapat dinilai.

Nyatakan sumber kode/aset dan bantuan AI. Maintainer meninjau kontribusi beserta hasil tes; tidak ada klaim penerimaan otomatis.
