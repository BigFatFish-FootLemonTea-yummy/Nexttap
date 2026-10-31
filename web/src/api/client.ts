import { ApiError, type LocateIn, type LocateOut, type ScreenshotOut } from '@/types/api'

/**
 * 默认走相对路径：开发由 Vite 代理到 127.0.0.1:8000，生产由 Nginx 同域反代。
 * 需要直连后端时在 .env.development 里设 VITE_API_BASE=http://127.0.0.1:8000
 * （后端 main.py 已放开 CORS，直连也能通）。
 */
const BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? ''

/** 定位接口 p90 约 4.8s，但冷启动/反光图会更慢，前端超时放宽到 60s */
const LOCATE_TIMEOUT_MS = 60_000
const UPLOAD_TIMEOUT_MS = 30_000

interface ErrorBody {
  detail?: unknown
}

/** FastAPI 的 422 有两种形态，必须分开：业务拒答是字符串，参数校验失败是数组 */
function extractDetail(body: ErrorBody | null): { text: string; isValidation: boolean } {
  const detail = body?.detail
  if (typeof detail === 'string') return { text: detail, isValidation: false }
  if (Array.isArray(detail)) {
    const text = detail
      .map((item) => {
        if (item && typeof item === 'object' && 'msg' in item) {
          return String((item as { msg: unknown }).msg)
        }
        return String(item)
      })
      .join('；')
    return { text: text || '请求参数不合法', isValidation: true }
  }
  return { text: '', isValidation: false }
}

function classify(status: number, isValidation: boolean): ApiError['kind'] {
  if (status === 422) return isValidation ? 'invalid' : 'refused'
  if (status === 404) return 'not-found'
  if (status === 413) return 'too-large'
  if (status === 400) return 'invalid'
  return 'server'
}

const FALLBACK_MESSAGE: Record<number, string> = {
  400: '图片格式不支持或无法解析，请换一张截图',
  404: '截图已不存在，请重新拍照上传',
  413: '图片超过 8MB，请先压缩再上传',
  422: '看不出该点哪里，请换一张更清楚的图',
  500: '服务端异常，请稍后再试',
  502: '定位结果异常，请重试一次',
}

async function parseError(res: Response): Promise<ApiError> {
  let body: ErrorBody | null = null
  try {
    body = (await res.json()) as ErrorBody
  } catch {
    body = null
  }
  const { text, isValidation } = extractDetail(body)
  const kind = classify(res.status, isValidation)
  const message = text || FALLBACK_MESSAGE[res.status] || `请求失败（${res.status}）`
  return new ApiError(res.status, kind, message)
}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number): Promise<Response> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(0, 'network', '等待超时，请检查网络后重试')
    }
    throw new ApiError(0, 'network', '连不上后端服务，请确认 uvicorn 已在 8000 端口启动')
  } finally {
    window.clearTimeout(timer)
  }
}

/** multipart 上传，字段名必须是 file（routes.py 写死） */
export async function uploadScreenshot(file: Blob, filename = 'screenshot.jpg'): Promise<ScreenshotOut> {
  const form = new FormData()
  form.append('file', file, filename)

  const res = await fetchWithTimeout(
    `${BASE}/api/screenshot`,
    { method: 'POST', body: form },
    UPLOAD_TIMEOUT_MS,
  )
  if (!res.ok) throw await parseError(res)
  return (await res.json()) as ScreenshotOut
}

export async function locate(input: LocateIn): Promise<LocateOut> {
  const res = await fetchWithTimeout(
    `${BASE}/api/locate`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    },
    LOCATE_TIMEOUT_MS,
  )
  if (!res.ok) throw await parseError(res)
  return (await res.json()) as LocateOut
}

export async function health(): Promise<{ ok: boolean; model: string; llm_key_set: boolean }> {
  const res = await fetchWithTimeout(`${BASE}/health`, { method: 'GET' }, 5_000)
  if (!res.ok) throw await parseError(res)
  return (await res.json()) as { ok: boolean; model: string; llm_key_set: boolean }
}
