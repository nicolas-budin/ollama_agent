import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

// Construit une fausse Response dont le body streame le texte SSE donné.
// `chunkSplitIndex`, si fourni, coupe le texte en deux `read()` séparés,
// pour vérifier que le buffering (bufferRef) recolle bien un événement
// dont les octets arrivent en plusieurs morceaux.
function makeSSEResponse(sseText, { chunkSplitIndex } = {}) {
  const encoder = new TextEncoder()
  const chunks =
    chunkSplitIndex == null
      ? [encoder.encode(sseText)]
      : [
          encoder.encode(sseText.slice(0, chunkSplitIndex)),
          encoder.encode(sseText.slice(chunkSplitIndex)),
        ]
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          async read() {
            if (i >= chunks.length) return { done: true, value: undefined }
            return { done: false, value: chunks[i++] }
          },
        }
      },
    },
  }
}

function stubFetch(chat) {
  return vi.fn(() => Promise.resolve(typeof chat === 'function' ? chat() : chat))
}

async function sendMessage(text) {
  const user = userEvent.setup()
  render(<App />)
  const input = await screen.findByPlaceholderText('Écris ton message...')
  await user.type(input, text)
  await user.click(screen.getByRole('button', { name: 'Envoyer' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('affiche le message utilisateur puis la réponse assistant streamée', async () => {
    const sse =
      'event: text\r\ndata: Bon\r\n\r\n' +
      'event: text\r\ndata: jour !\r\n\r\n' +
      'event: done\r\ndata: {"duration_ms": 456, "eval_count": 42}\r\n\r\n'
    vi.stubGlobal('fetch', stubFetch(makeSSEResponse(sse)))

    await sendMessage('Salut')

    expect(screen.getByText('Salut')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Bonjour !')).toBeInTheDocument())
    expect(screen.getByText('Durée : 456 ms · 42 tokens')).toBeInTheDocument()
  })

  it('affiche un message d’erreur si le serveur répond avec un statut non-ok', async () => {
    vi.stubGlobal('fetch', stubFetch({ ok: false, status: 500 }))

    await sendMessage('Salut')

    await waitFor(() => expect(screen.getByText('Erreur : 500')).toBeInTheDocument())
  })

  it('affiche l’événement error émis par le serveur', async () => {
    const sse = 'event: error\r\ndata: Boom\r\n\r\n'
    vi.stubGlobal('fetch', stubFetch(makeSSEResponse(sse)))

    await sendMessage('Salut')

    await waitFor(() => expect(screen.getByText('Erreur : Boom')).toBeInTheDocument())
  })

  it('reconstruit un événement dont les octets arrivent en deux morceaux (buffering)', async () => {
    const sse = 'event: text\r\ndata: Bonjour !\r\n\r\nevent: done\r\ndata: {"duration_ms": 1, "eval_count": 0}\r\n\r\n'
    // Coupe en plein milieu de la ligne "data: Bonjour !" pour vérifier que
    // bufferRef recolle correctement les deux morceaux avant de parser.
    const cutPoint = sse.indexOf('Bon') + 2
    vi.stubGlobal('fetch', stubFetch(makeSSEResponse(sse, { chunkSplitIndex: cutPoint })))

    await sendMessage('Salut')

    await waitFor(() => expect(screen.getByText('Bonjour !')).toBeInTheDocument())
  })

  it('vide la conversation avec le bouton "Nouvelle conversation"', async () => {
    const sse = 'event: text\r\ndata: Bonjour !\r\n\r\nevent: done\r\ndata: {"duration_ms": 1, "eval_count": 1}\r\n\r\n'
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(makeSSEResponse(sse))
      .mockResolvedValueOnce({ ok: true, status: 200 })
    vi.stubGlobal('fetch', fetchMock)

    await sendMessage('Salut')
    await waitFor(() => expect(screen.getByText('Bonjour !')).toBeInTheDocument())

    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'Nouvelle conversation' }))

    expect(fetchMock).toHaveBeenLastCalledWith('/api/reset', { method: 'POST' })
    await waitFor(() => expect(screen.queryByText('Salut')).not.toBeInTheDocument())
    expect(screen.queryByText('Bonjour !')).not.toBeInTheDocument()
  })

  it('n’envoie pas de requête si le champ est vide', async () => {
    vi.stubGlobal('fetch', stubFetch({ ok: true, status: 200 }))
    const user = userEvent.setup()
    render(<App />)

    await screen.findByPlaceholderText('Écris ton message...')
    await user.click(screen.getByRole('button', { name: 'Envoyer' }))

    expect(fetch).not.toHaveBeenCalled()
  })
})
