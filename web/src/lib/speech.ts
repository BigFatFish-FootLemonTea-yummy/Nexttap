/**
 * 语音播报（定稿 F5：SpeechSynthesis）。
 * 不引入任何云端 TTS：现场网络不可控，且播报内容涉及截图文字。
 * 不支持时静默降级——大字卡片本身已经够用，不弹错误打扰老人。
 */
export function useSpeech() {
  const supported = typeof window !== 'undefined' && 'speechSynthesis' in window

  function speak(text: string) {
    if (!supported || !text.trim()) return
    const synth = window.speechSynthesis
    synth.cancel()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = 'zh-CN'
    utterance.rate = 0.85 // 老人听清比语速快重要
    const voice = synth.getVoices().find((v) => v.lang.toLowerCase().startsWith('zh'))
    if (voice) utterance.voice = voice
    synth.speak(utterance)
  }

  function stop() {
    if (supported) window.speechSynthesis.cancel()
  }

  return { supported, speak, stop }
}
