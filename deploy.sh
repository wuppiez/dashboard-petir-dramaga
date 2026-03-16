#!/bin/bash
# ============================================================
# deploy.sh – Script otomatis upload ke GitHub
# Cara pakai:
#   1. Isi GITHUB_USERNAME di bawah
#   2. Jalankan: bash deploy.sh
# ============================================================

GITHUB_USERNAME="GANTI_DENGAN_USERNAME_GITHUB_ANDA"
REPO_NAME="dashboard-petir-dramaga"

echo "======================================"
echo " Deploy Dashboard Petir ke GitHub"
echo "======================================"

# Cek apakah git sudah terinstall
if ! command -v git &> /dev/null; then
    echo "❌ Git belum terinstall."
    echo "   Download di: https://git-scm.com/downloads"
    exit 1
fi

# Inisialisasi git jika belum
if [ ! -d ".git" ]; then
    echo "📁 Menginisialisasi Git..."
    git init
    git branch -M main
fi

# Tambahkan remote jika belum ada
if ! git remote get-url origin &> /dev/null; then
    echo "🔗 Menghubungkan ke GitHub..."
    git remote add origin https://github.com/$GITHUB_USERNAME/$REPO_NAME.git
fi

# Stage semua file
echo "📦 Menyiapkan file..."
git add .

# Commit
TIMESTAMP=$(date "+%Y-%m-%d %H:%M")
git commit -m "Update dashboard $TIMESTAMP"

# Push ke GitHub
echo "🚀 Mengupload ke GitHub..."
git push -u origin main

echo ""
echo "✅ SELESAI! File sudah terupload ke GitHub."
echo "   Render.com akan otomatis deploy dalam 2-3 menit."
echo ""
echo "🌐 URL Dashboard (setelah deploy):"
echo "   https://$REPO_NAME.onrender.com"
