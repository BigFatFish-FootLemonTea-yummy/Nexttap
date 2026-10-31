/**
 * 前端压缩（定稿 F1：长边 ≤1600）。
 * 目的不是省流量，而是把 4000×3000 的拍照图降到后端能稳定处理、
 * 且 8MB 上限（MAX_UPLOAD_MB）绝不会触发的体积。
 */

export const MAX_LONG_EDGE = 1600
const JPEG_QUALITY = 0.85
const MAX_BYTES = 8 * 1024 * 1024

export interface CompressedImage {
  blob: Blob
  /** 直接当 <img src> 用，同时可存 sessionStorage 让刷新后不丢图 */
  dataUrl: string
  width: number
  height: number
  bytes: number
  originalBytes: number
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('图片读取失败'))
    reader.readAsDataURL(blob)
  })
}

async function decode(file: Blob): Promise<{ source: CanvasImageSource; width: number; height: number; close?: () => void }> {
  // createImageBitmap 能按 EXIF 方向纠正手机拍照的旋转，<img> 解码在部分浏览器会躺倒
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
      return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() }
    } catch {
      // 落到下面的 <img> 路径
    }
  }
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('图片无法解析，请换一张'))
      el.src = url
    })
    return {
      source: img,
      width: img.naturalWidth,
      height: img.naturalHeight,
      close: () => URL.revokeObjectURL(url),
    }
  } catch (err) {
    URL.revokeObjectURL(url)
    throw err
  }
}

function drawToCanvas(source: CanvasImageSource, width: number, height: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前浏览器不支持图片压缩')
  ctx.drawImage(source, 0, 0, width, height)
  return canvas
}

function canvasToBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('图片压缩失败'))),
      'image/jpeg',
      quality,
    )
  })
}

export async function compressImage(file: Blob): Promise<CompressedImage> {
  const decoded = await decode(file)
  try {
    const longEdge = Math.max(decoded.width, decoded.height)
    const scale = Math.min(1, MAX_LONG_EDGE / longEdge)
    const width = Math.max(1, Math.round(decoded.width * scale))
    const height = Math.max(1, Math.round(decoded.height * scale))

    const canvas = drawToCanvas(decoded.source, width, height)
    let quality = JPEG_QUALITY
    let blob = await canvasToBlob(canvas, quality)
    // 极端情况下（细节特别多的大图）继续降质量，避免被后端 413 拒掉
    while (blob.size > MAX_BYTES && quality > 0.4) {
      quality -= 0.1
      blob = await canvasToBlob(canvas, quality)
    }

    return {
      blob,
      dataUrl: await blobToDataUrl(blob),
      width,
      height,
      bytes: blob.size,
      originalBytes: file.size,
    }
  } finally {
    decoded.close?.()
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}
