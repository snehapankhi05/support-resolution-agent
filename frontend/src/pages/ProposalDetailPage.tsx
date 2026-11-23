import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { ApiError } from '../api'
import { truncateId } from '../api/orders'
import {
  decideProposal,
  getProposal,
  type ProposalDecision,
  type ProposalReview,
} from '../api/proposals'
import { formatPaiseAsInr } from '../lib/money'
import { productNamesText } from '../lib/productLabel'
import {
  classifyDecisionFailure,
  classifyDecisionSuccess,
  decisionLocksActions,
  type DecisionClassification,
} from '../lib/proposalDecision'
import { formatCreatedAt } from '../lib/time'

/**
 * Evidence vs customer context stay separate. Decision body is approve|reject only.
 */
export function ProposalDetailPage() {
  const { proposalId } = useParams<{ proposalId: string }>()
  const navigate = useNavigate()
  const noticeId = useId()

  const [review, setReview] = useState<ProposalReview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [locked, setLocked] = useState(false)
  const [lastDecision, setLastDecision] = useState<ProposalDecision | null>(null)
  const [notice, setNotice] = useState<{
    tone: 'ok' | 'warn' | 'danger' | 'neutral'
    text: string
    showRetry: boolean
  } | null>(null)

  const inFlight = useRef(false)

  const load = useCallback(async () => {
    if (!proposalId) {
      setLoadError('Proposal not found')
      setReview(null)
      return
    }
    setLoading(true)
    setLoadError(null)
    try {
      const row = await getProposal(proposalId)
      setReview(row)
      if (row.proposal_evidence.status !== 'pending_approval') {
        setLocked(true)
        setNotice({
          tone: 'neutral',
          text: `This proposal is already ${row.proposal_evidence.status}.`,
          showRetry: false,
        })
      }
    } catch (err: unknown) {
      setReview(null)
      setLoadError(err instanceof ApiError ? err.detail : 'Could not load this proposal')
    } finally {
      setLoading(false)
    }
  }, [proposalId])

  useEffect(() => {
    void load()
  }, [load])

  function applyClassification(
    result: DecisionClassification,
    attempted: ProposalDecision,
  ) {
    if (decisionLocksActions(result)) {
      setLocked(true)
    }

    if (result.kind === 'recorded' && result.source === 'success') {
      setNotice({
        tone: 'ok',
        text: `Decision saved (${result.response.proposal_status}). Returning to queue…`,
        showRetry: false,
      })
      window.setTimeout(() => navigate('/review/queue'), 900)
      return
    }

    if (result.kind === 'recorded' && result.source === 'enqueue_failed') {
      setNotice({
        tone: 'warn',
        text: result.detail,
        showRetry: true,
      })
      return
    }

    if (result.kind === 'recorded' && result.source === 'already_recorded') {
      setNotice({
        tone: 'neutral',
        text: 'A decision was already recorded for this proposal. Resume was re-queued when possible.',
        showRetry: false,
      })
      window.setTimeout(() => navigate('/review/queue'), 1200)
      return
    }

    if (result.kind === 'not_pending') {
      setNotice({
        tone: 'neutral',
        text: result.detail,
        showRetry: false,
      })
      void (async () => {
        if (!proposalId) return
        try {
          const row = await getProposal(proposalId)
          setReview(row)
          if (row.proposal_evidence.status !== 'pending_approval') {
            setLocked(true)
            setNotice({
              tone: 'neutral',
              text: `This proposal is already ${row.proposal_evidence.status}.`,
              showRetry: false,
            })
          } else {
            setLocked(false)
            setNotice({ tone: 'danger', text: result.detail, showRetry: false })
          }
        } catch {
          setLocked(true)
        }
      })()
      return
    }

    // conflict or error — unlock unless we somehow locked
    setLocked(false)
    setNotice({
      tone: 'danger',
      text: result.detail,
      showRetry: false,
    })
    void attempted
  }

  async function submitDecision(decision: ProposalDecision) {
    if (!proposalId || inFlight.current || locked) return
    inFlight.current = true
    setSubmitting(true)
    setLastDecision(decision)
    setNotice(null)
    try {
      const body = await decideProposal(proposalId, decision)
      applyClassification(classifyDecisionSuccess(body), decision)
    } catch (err: unknown) {
      applyClassification(classifyDecisionFailure(err), decision)
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  async function retryResume() {
    if (!proposalId || lastDecision === null || inFlight.current) return
    inFlight.current = true
    setSubmitting(true)
    try {
      const body = await decideProposal(proposalId, lastDecision)
      applyClassification(classifyDecisionSuccess(body), lastDecision)
    } catch (err: unknown) {
      applyClassification(classifyDecisionFailure(err), lastDecision)
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  const busy = submitting || loading
  const actionsDisabled = busy || locked || review === null

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-text-muted">
            Proposal
          </p>
          <h1 className="font-brand text-2xl font-semibold text-ink">
            {proposalId ? truncateId(proposalId) : '—'}
          </h1>
        </div>
        <Link
          to="/review/queue"
          className="text-sm font-medium text-accent hover:text-accent-hover"
        >
          Back to queue
        </Link>
      </header>

      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-danger/20 bg-danger-soft px-3 py-2 text-sm text-danger"
        >
          {loadError}
        </p>
      ) : null}

      {loading && review === null && loadError === null ? (
        <p className="text-sm text-text-muted" aria-live="polite">
          Loading proposal…
        </p>
      ) : null}

      {notice ? (
        <div
          id={noticeId}
          role="status"
          className={`rounded-md border px-3 py-2 text-sm ${noticeToneClass(notice.tone)}`}
        >
          <p>{notice.text}</p>
          {notice.showRetry ? (
            <button
              type="button"
              disabled={submitting || lastDecision === null}
              onClick={() => void retryResume()}
              className="mt-2 min-h-10 rounded-md border border-warn/40 bg-paper px-3 py-1.5 text-sm font-medium text-warn disabled:opacity-60"
            >
              {submitting ? 'Retrying…' : 'Retry resume'}
            </button>
          ) : null}
        </div>
      ) : null}

      {review !== null ? (
        <>
          <div className="grid gap-5 lg:grid-cols-2">
            <section className="rounded-lg border border-line bg-fog/40 p-4 sm:p-5">
              <h2 className="text-sm font-semibold text-ink">Evidence</h2>
              <p className="mt-1 text-xs text-text-muted">
                Stored proposal payload — not recomputed in the browser.
              </p>
              <dl className="mt-4 grid gap-3 text-sm">
                <Field
                  label="Amount"
                  value={formatPaiseAsInr(review.proposal_evidence.amount_minor)}
                  mono
                />
                <Field
                  label="Order"
                  value={truncateId(review.proposal_evidence.order_id)}
                  mono
                />
                <Field
                  label="Product"
                  value={productNamesText(review.product_names) ?? '—'}
                />
                <Field label="Policy effect" value={review.proposal_evidence.policy_effect} />
                <Field
                  label="Policy versions"
                  value={
                    review.proposal_evidence.policy_versions.length === 0
                      ? '—'
                      : review.proposal_evidence.policy_versions
                          .map((v) => `${v.policy_key}@${v.version}`)
                          .join(', ')
                  }
                />
                <Field
                  label="Created"
                  value={formatCreatedAt(review.proposal_evidence.created_at)}
                />
                <Field label="Status" value={review.proposal_evidence.status} />
                <Field
                  label="Payload hash"
                  value={truncateId(review.proposal_evidence.payload_hash, 10)}
                  mono
                />
              </dl>
            </section>

            <section className="rounded-lg border border-dashed border-line bg-paper p-4 sm:p-5">
              <h2 className="text-sm font-semibold text-ink">Customer context</h2>
              <p className="mt-1 text-xs text-text-muted">
                Untrusted complaint text — not financial evidence.
              </p>
              <dl className="mt-4 grid gap-3 text-sm">
                <Field
                  label="Ticket"
                  value={truncateId(review.customer_context.ticket_id)}
                  mono
                />
                <div>
                  <dt className="text-text-muted">Summary</dt>
                  <dd className="mt-1 whitespace-pre-wrap break-words text-text">
                    {review.customer_context.summary}
                  </dd>
                </div>
              </dl>
            </section>
          </div>

          <div className="sticky bottom-0 z-10 -mx-1 border-t border-line bg-paper/95 px-1 py-4 backdrop-blur-sm">
            <p className="mb-3 text-xs text-text-muted">
              Decision only — amount is set by the backend.
            </p>
            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                disabled={actionsDisabled}
                aria-describedby={notice ? noticeId : undefined}
                onClick={() => void submitDecision('reject')}
                className="min-h-11 min-w-[8rem] rounded-md border border-danger/40 px-4 py-2.5 text-sm font-semibold text-danger hover:bg-danger-soft disabled:opacity-60"
              >
                {submitting && lastDecision === 'reject' ? 'Rejecting…' : 'Reject'}
              </button>
              <button
                type="button"
                disabled={actionsDisabled}
                aria-describedby={notice ? noticeId : undefined}
                onClick={() => void submitDecision('approve')}
                className="min-h-11 min-w-[8rem] rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-text-on-ink hover:bg-accent-hover disabled:opacity-60"
              >
                {submitting && lastDecision === 'approve' ? 'Approving…' : 'Approve'}
              </button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}

function Field({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div>
      <dt className="text-text-muted">{label}</dt>
      <dd className={`mt-0.5 text-text ${mono ? 'font-mono text-xs tabular-nums' : ''}`}>
        {value}
      </dd>
    </div>
  )
}

function noticeToneClass(tone: 'ok' | 'warn' | 'danger' | 'neutral'): string {
  switch (tone) {
    case 'ok':
      return 'border-ok/30 bg-ok-soft text-ok'
    case 'warn':
      return 'border-warn/30 bg-warn-soft text-warn'
    case 'danger':
      return 'border-danger/20 bg-danger-soft text-danger'
    default:
      return 'border-line bg-fog text-text'
  }
}
