#!/bin/bash

EXIFTOOL_FILE_NAME="Image-ExifTool-12.92.tar.gz"
EXIFTOOL_FILE_DOWNLOAD_URL="http://file.lsvm.xyz/Image-ExifTool-12.92.tar.gz"

if [ -f "inited" ]; then
  echo "已完成初始化, 开始运行(如需重新初始化, 请删除 inited 文件)"
  exit 0
fi

# 下载文件
curl -O -L $EXIFTOOL_FILE_DOWNLOAD_URL

# 测试 gzip 压缩的有效性
if ! gzip -t "$EXIFTOOL_FILE_NAME"; then
    echo "下载的 ExifTool gzip 压缩文件格式不正确"
    echo "请检查 url 的有效性： $EXIFTOOL_FILE_DOWNLOAD_URL"
    echo "当前下载的 ExifTool gzip 的格式为："
    file "$EXIFTOOL_FILE_NAME"
    echo "安装未完成，初始化脚本中断"
    exit 1
fi

# 创建目录
mkdir -p ./exiftool

# 解压文件
tar -xzf "$EXIFTOOL_FILE_NAME" -C ./exiftool --strip-components=1

# 删除压缩包
rm "$EXIFTOOL_FILE_NAME"

# 下载 python 依赖
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装 HEIC 系统依赖 libheif（pillow-heif 运行时需要；JPEG 功能不受影响）
install_libheif() {
  if command -v brew >/dev/null 2>&1; then
    if ! brew list libheif >/dev/null 2>&1; then
      echo "正在安装 libheif（用于 HEIC 输出）..."
      brew install libheif
    else
      echo "libheif 已安装"
    fi
  elif command -v apt-get >/dev/null 2>&1; then
    if ! dpkg -s libheif-dev >/dev/null 2>&1; then
      echo "正在安装 libheif（用于 HEIC 输出），可能需要输入 sudo 密码..."
      sudo apt-get update && sudo apt-get install -y libheif-dev
    else
      echo "libheif 已安装"
    fi
  else
    echo "警告: 未检测到 brew/apt-get，跳过 libheif 安装。HEIC 输出需要 libheif，仅用 JPEG 则不受影响。"
  fi
}
install_libheif

# 初始化完成
touch inited
echo "初始化完成, inited 文件已生成, 如需重新初始化, 请删除 inited 文件"
exit 0
