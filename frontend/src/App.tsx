import { useState } from 'react';
import { withAuthenticator, WithAuthenticatorProps } from '@aws-amplify/ui-react';
import SuggestionListView from './views/SuggestionListView';
import MetricsView from './views/MetricsView';

type Tab = 'suggestions' | 'metrics';

function App({ signOut, user }: WithAuthenticatorProps) {
  const [tab, setTab] = useState<Tab>('suggestions');

  return (
    <div className="app">
      <header className="app-header">
        <h1>au Jibun Bank — 管理ダッシュボード</h1>
        <div className="user-bar">
          <span>{user?.signInDetails?.loginId ?? user?.username}</span>
          <button type="button" data-testid="sign-out-button" onClick={signOut}>
            サインアウト
          </button>
        </div>
      </header>

      <nav className="tabs">
        <button
          type="button"
          data-testid="tab-suggestions"
          className={tab === 'suggestions' ? 'active' : ''}
          onClick={() => setTab('suggestions')}
        >
          改善提案
        </button>
        <button
          type="button"
          data-testid="tab-metrics"
          className={tab === 'metrics' ? 'active' : ''}
          onClick={() => setTab('metrics')}
        >
          利用統計
        </button>
      </nav>

      <main>{tab === 'suggestions' ? <SuggestionListView /> : <MetricsView />}</main>
    </div>
  );
}

// Wrap with the Cognito Authenticator HOC (MFA TOTP is optional, configured on
// the UserPool). Unauthenticated users see the sign-in screen.
export default withAuthenticator(App);
