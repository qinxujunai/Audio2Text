// Cloudflare Worker — reverse-proxy + CN forward-proxy + free Whisper transcription.
//
// Reverse proxy: all regular requests → HF Space (China → HF Space via workers.dev)
// Forward proxy: /__proxy__?url=ENCODED_URL → fetch CN platform (HF Space → CN via Worker)
// Transcription: /__transcribe__ (POST audio file) → Workers AI Whisper (free tier)
//
// Deploy: cd scripts && npx wrangler deploy

const TARGET_HOST = 'liamgrant-wanxiang-chengwen-preview.hf.space'

async function handleReverseProxy(request) {
  const url = new URL(request.url)
  url.hostname = TARGET_HOST

  const headers = new Headers(request.headers)
  headers.set('Host', TARGET_HOST)
  const cfConnectingIp = request.headers.get('CF-Connecting-IP')
  if (cfConnectingIp) {
    headers.set('X-Forwarded-For', cfConnectingIp)
    headers.set('X-Real-IP', cfConnectingIp)
  }

  const upstream = await fetch(url.toString(), {
    method: request.method,
    headers,
    body: request.body,
    redirect: 'follow',
  })

  const responseHeaders = new Headers(upstream.headers)
  responseHeaders.set('Access-Control-Allow-Origin', '*')
  responseHeaders.set('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
  responseHeaders.set('Access-Control-Allow-Headers', '*')

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  })
}

async function handleForwardProxy(request) {
  const proxyUrl = new URL(request.url).searchParams.get('url')
  if (!proxyUrl) {
    return new Response('missing url parameter', { status: 400 })
  }

  try {
    const targetUrl = new URL(proxyUrl)
    const headers = new Headers()
    headers.set('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0 Safari/537.36')
    headers.set('Accept', 'text/html,application/json,*/*')

    const upstream = await fetch(targetUrl.toString(), {
      method: 'GET',
      headers,
      redirect: 'follow',
    })

    const body = await upstream.text()
    const responseHeaders = new Headers()
    responseHeaders.set('Content-Type', 'text/plain; charset=utf-8')
    responseHeaders.set('Access-Control-Allow-Origin', '*')
    responseHeaders.set('X-Proxied-Url', upstream.url)
    responseHeaders.set('X-Proxied-Status', String(upstream.status))

    return new Response(body, {
      status: 200,
      headers: responseHeaders,
    })
  } catch (err) {
    return new Response(`proxy error: ${err.message}`, { status: 502 })
  }
}

async function handleTranscribe(request, env) {
  if (request.method !== 'POST') {
    return new Response('Method not allowed', {
      status: 405,
      headers: { 'Access-Control-Allow-Origin': '*' },
    })
  }

  try {
    const contentType = request.headers.get('Content-Type') || ''
    let audioArray

    if (contentType.includes('multipart/form-data')) {
      const formData = await request.formData()
      const file = formData.get('file')
      if (!file) {
        return new Response('Missing file field', {
          status: 400,
          headers: { 'Access-Control-Allow-Origin': '*' },
        })
      }
      const buffer = await file.arrayBuffer()
      audioArray = [...new Uint8Array(buffer)]
    } else {
      const buffer = await request.arrayBuffer()
      audioArray = [...new Uint8Array(buffer)]
    }

    const result = await env.AI.run('@cf/openai/whisper', { audio: audioArray })

    return new Response(JSON.stringify({
      text: result.text || '',
    }), {
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
      },
    })
  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), {
      status: 500,
      headers: {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
      },
    })
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url)

    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
          'Access-Control-Allow-Headers': '*',
        },
      })
    }

    if (url.pathname === '/__proxy__') {
      return handleForwardProxy(request)
    }

    if (url.pathname === '/__transcribe__') {
      return handleTranscribe(request, env)
    }

    return handleReverseProxy(request)
  },
}
