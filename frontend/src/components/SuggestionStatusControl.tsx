import { useState } from 'react';

export interface SuggestionStatusControlProps {
  suggestionId: string;
  currentStatus: string;
  disabled?: boolean;
  onChange: (status: 'approved' | 'rejected' | 'hold', rejectReason?: string) => void;
}

/**
 * Approve / reject / hold control for a single suggestion. Rejecting reveals a
 * free-text reason field which is sent alongside the status change.
 */
export function SuggestionStatusControl({
  suggestionId,
  currentStatus,
  disabled = false,
  onChange,
}: SuggestionStatusControlProps) {
  const [showReject, setShowReject] = useState(false);
  const [reason, setReason] = useState('');

  return (
    <div data-testid={`status-control-${suggestionId}`} className="status-control">
      <span className="status-label">{currentStatus}</span>
      <button
        type="button"
        data-testid="status-button-approve"
        disabled={disabled}
        onClick={() => onChange('approved')}
      >
        承認
      </button>
      <button
        type="button"
        data-testid="status-button-reject"
        disabled={disabled}
        onClick={() => setShowReject((v) => !v)}
      >
        却下
      </button>
      <button
        type="button"
        data-testid="status-button-hold"
        disabled={disabled}
        onClick={() => onChange('hold')}
      >
        保留
      </button>

      {showReject && (
        <div className="reject-form">
          <input
            type="text"
            data-testid={`reject-reason-${suggestionId}`}
            placeholder="却下理由を入力"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
          <button
            type="button"
            data-testid={`reject-confirm-${suggestionId}`}
            disabled={disabled}
            onClick={() => {
              onChange('rejected', reason.trim() || undefined);
              setShowReject(false);
              setReason('');
            }}
          >
            却下を確定
          </button>
        </div>
      )}
    </div>
  );
}

export default SuggestionStatusControl;
