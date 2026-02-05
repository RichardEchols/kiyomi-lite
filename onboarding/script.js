/**
 * Kiyomi Onboarding Wizard
 * Subscription-first flow: connect existing AI subscriptions via CLI OAuth.
 * API key entry is an advanced fallback.
 */

let currentStep = 1;
let selectedProvider = '';       // API key provider (advanced fallback)
let selectedSubscription = '';   // CLI subscription provider
let cliStatus = {};              // { claude: {installed, authenticated, ...}, ... }
let importedFile = null;
let claimedBot = null;           // Reserved for future use

// Copy text to clipboard
function copyText(text, element) {
    if (text.startsWith('kiyomi_') && text.endsWith('_bot')) {
        text = suggestedBotUsername;
    }
    navigator.clipboard.writeText(text).then(() => {
        element.classList.add('copied');
        const hint = element.querySelector('.copy-hint');
        if (hint) {
            const orig = hint.textContent;
            hint.textContent = 'Copied!';
            setTimeout(() => {
                element.classList.remove('copied');
                hint.textContent = orig;
            }, 2000);
        }
    }).catch(() => {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        element.classList.add('copied');
        const hint = element.querySelector('.copy-hint');
        if (hint) {
            const orig = hint.textContent;
            hint.textContent = 'Copied!';
            setTimeout(() => {
                element.classList.remove('copied');
                hint.textContent = orig;
            }, 2000);
        }
    });
}

const config = {
    name: '',
    provider: '',
    cli_provider: '',
    gemini_key: '',
    anthropic_key: '',
    openai_key: '',
    telegram_token: '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    imported_chats: false,
    setup_complete: false,
};

// ── Navigation ─────────────────────────────────────────────

function showStep(step) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.dot').forEach(d => d.classList.remove('active'));

    const stepEl = document.getElementById(`step-${step}`);
    const dotEl = document.querySelector(`.dot[data-step="${step}"]`);

    if (stepEl) stepEl.classList.add('active');
    if (dotEl) dotEl.classList.add('active');

    currentStep = step;

    // Auto-detect CLIs when entering step 2
    if (step === 2) {
        detectCLIs();
    }

    // Step 3: Telegram token entry (BotFather flow)
    if (step === 3) {
        const manualEl = document.getElementById('tg-manual');
        if (manualEl) manualEl.style.display = 'block';
    }

    setTimeout(() => {
        const input = stepEl?.querySelector('input:not([type="file"])');
        if (input) input.focus();
    }, 100);
}

async function nextStep(from) {
    if (!validateStep(from)) return;
    saveStepData(from);

    // After step 3 (Telegram), all essential config is collected.
    // Save config + start engine immediately so the bot works
    // even if user doesn't finish steps 4-5.
    if (from === 3) {
        await saveConfigAndStartEngine();
    }

    if (from === 4 && importedFile) {
        await uploadAndImport();
    }

    showStep(from + 1);

    if (from + 1 === 5) {
        finish();
    }
}

function prevStep(from) {
    showStep(from - 1);
}

// ── Validation ─────────────────────────────────────────────

function validateStep(step) {
    switch (step) {
        case 1: {
            const name = document.getElementById('user-name').value.trim();
            if (!name) {
                shake(document.getElementById('user-name'));
                return false;
            }
            return true;
        }
        case 2: {
            // Valid if: subscription connected OR API key entered
            if (selectedSubscription && cliStatus[selectedSubscription]?.authenticated) {
                return true;
            }
            const key = document.getElementById('api-key').value.trim();
            if (key && selectedProvider) {
                return true;
            }
            // Nothing selected — shake the grid
            const grid = document.getElementById('sub-grid');
            if (grid) shake(grid);
            return false;
        }
        case 3: {
            const tokenEl = document.getElementById('tg-token');
            if (tokenEl) {
                const token = tokenEl.value.trim();
                if (!token || !token.includes(':')) {
                    shake(tokenEl);
                    return false;
                }
                return true;
            }
            return false;
        }
        default:
            return true;
    }
}

function shake(element) {
    element.style.borderColor = '#FF3B30';
    element.style.animation = 'shake 0.4s ease';
    setTimeout(() => {
        element.style.borderColor = '';
        element.style.animation = '';
    }, 600);
}

const style = document.createElement('style');
style.textContent = `
    @keyframes shake {
        0%, 100% { transform: translateX(0); }
        25% { transform: translateX(-6px); }
        75% { transform: translateX(6px); }
    }
`;
document.head.appendChild(style);

// ── Save step data ─────────────────────────────────────────

function saveStepData(step) {
    switch (step) {
        case 1:
            config.name = document.getElementById('user-name').value.trim();
            document.getElementById('done-name').textContent = config.name;
            break;
        case 2: {
            // Subscription-based (CLI)
            if (selectedSubscription && cliStatus[selectedSubscription]?.authenticated) {
                config.cli_provider = selectedSubscription;
                config.provider = selectedSubscription + '-cli';
            }
            // API key fallback
            const key = document.getElementById('api-key').value.trim();
            if (key && selectedProvider) {
                config.provider = selectedProvider;
                config[`${selectedProvider}_key`] = key;
            }
            break;
        }
        case 3: {
            const tokenEl = document.getElementById('tg-token');
            config.telegram_token = tokenEl ? tokenEl.value.trim() : '';
            break;
        }
    }
}

// ── CLI Detection (background only — no UI display) ───────

async function detectCLIs() {
    try {
        const resp = await fetch('/api/cli/status');
        if (!resp.ok) throw new Error('Status check failed');
        const data = await resp.json();
        cliStatus = data.providers || {};
        // No auto-display, no auto-select — customer always starts fresh
    } catch (e) {
        console.log('CLI detection unavailable:', e);
    }
}

// ── Subscription Selection ─────────────────────────────────

function selectSubscription(provider) {
    selectedSubscription = provider;

    // Highlight selected card
    document.querySelectorAll('#sub-grid .provider-card').forEach(c => c.classList.remove('selected'));
    const card = document.getElementById(`sub-${provider}`);
    if (card) card.classList.add('selected');

    const panel = document.getElementById('connect-panel');
    const checking = document.getElementById('connect-checking');
    const ready = document.getElementById('connect-ready');
    const needsAuth = document.getElementById('connect-needs-auth');

    panel.style.display = 'block';
    checking.style.display = 'none';
    ready.style.display = 'none';
    needsAuth.style.display = 'none';

    // Always show "Sign In" — clean experience for every user
    needsAuth.style.display = 'block';
    const names = { claude: 'Claude', codex: 'ChatGPT', gemini: 'Google Gemini' };
    const name = names[provider] || provider;
    document.getElementById('auth-title').textContent = `Sign in to ${name}`;
    document.getElementById('auth-desc').textContent =
        'Click below to sign in with your subscription account. A browser window will open.';
    document.getElementById('btn-auth').textContent = 'Sign In';
}

async function triggerAuth() {
    if (!selectedSubscription) return;

    const panel = document.getElementById('connect-panel');
    const checking = document.getElementById('connect-checking');
    const ready = document.getElementById('connect-ready');
    const needsAuth = document.getElementById('connect-needs-auth');

    checking.style.display = 'block';
    needsAuth.style.display = 'none';
    ready.style.display = 'none';

    const info = cliStatus[selectedSubscription];
    const names = { claude: 'Claude', codex: 'ChatGPT', gemini: 'Google Gemini' };
    const name = names[selectedSubscription] || selectedSubscription;

    try {
        // Step 1: Install if needed
        if (!info || !info.installed) {
            document.getElementById('connect-action').textContent = `Installing ${name}...`;
            document.getElementById('connect-hint').textContent = 'This only happens once.';

            const installResp = await fetch('/api/cli/install', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider: selectedSubscription }),
            });
            const installData = await installResp.json();

            if (!installData.success) {
                throw new Error(installData.error || 'Installation failed');
            }

            // Update local status
            if (!cliStatus[selectedSubscription]) cliStatus[selectedSubscription] = {};
            cliStatus[selectedSubscription].installed = true;
        }

        // Step 2: Trigger auth (install or open browser sign-in)
        document.getElementById('connect-action').textContent = `Connecting ${name}...`;
        document.getElementById('connect-hint').textContent = 'Sign in with your subscription account in the browser.';

        const authResp = await fetch('/api/cli/auth', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: selectedSubscription }),
        });
        const authData = await authResp.json();

        // Re-check status to get account info
        const statusResp = await fetch('/api/cli/status');
        const statusData = await statusResp.json();
        cliStatus = statusData.providers || {};

        checking.style.display = 'none';
        if (cliStatus[selectedSubscription]?.authenticated) {
            const acct = cliStatus[selectedSubscription].account;
            ready.style.display = 'block';
            document.getElementById('connect-result').textContent =
                `✓ Connected as ${acct || name}`;
            document.getElementById('connect-account').textContent =
                `Using your ${cliStatus[selectedSubscription].subscription || name} subscription`;
            document.getElementById('connect-account').style.display = 'block';
            document.getElementById('btn-switch-account').style.display = 'none';
        } else {
            // Auth might still be in progress
            needsAuth.style.display = 'block';
            document.getElementById('auth-title').textContent = 'Almost there!';
            document.getElementById('auth-desc').textContent =
                authData.detail || 'Complete the sign-in in your browser, then click below to check again.';
            document.getElementById('btn-auth').textContent = 'Check Again';
        }
    } catch (e) {
        console.error('Auth error:', e);
        checking.style.display = 'none';
        needsAuth.style.display = 'block';
        document.getElementById('auth-title').textContent = 'Something went wrong';
        document.getElementById('auth-desc').textContent = e.message || 'Please try again.';
        document.getElementById('btn-auth').textContent = 'Try Again';
    }
}

// ── API Key Provider Selection (Advanced fallback) ─────────

function selectProvider(provider) {
    selectedProvider = provider;

    document.querySelectorAll('#api-key-fallback .provider-card').forEach(card => {
        card.classList.remove('selected');
    });
    const el = document.getElementById(`prov-${provider}`);
    if (el) el.classList.add('selected');

    document.getElementById('api-key').value = '';
    document.getElementById('api-key').focus();
}

// ── Bot Pool removed — customers create their own bot via BotFather ──

// ── File import ────────────────────────────────────────────

function handleDragOver(event) {
    event.preventDefault();
    document.getElementById('drop-zone').classList.add('dragover');
}

function handleDragLeave(event) {
    document.getElementById('drop-zone').classList.remove('dragover');
}

function handleDrop(event) {
    event.preventDefault();
    document.getElementById('drop-zone').classList.remove('dragover');
    const files = event.dataTransfer.files;
    if (files.length > 0) processFile(files[0]);
}

function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) processFile(files[0]);
}

function processFile(file) {
    importedFile = file;
    const status = document.getElementById('import-status');
    status.style.display = 'block';
    const sizeMB = (file.size / 1024 / 1024).toFixed(1);

    if (file.name.endsWith('.json') || file.name.endsWith('.zip')) {
        status.className = 'import-status success';
        status.innerHTML = `<strong>${file.name}</strong> (${sizeMB}MB) — Ready to import!`;
        config.imported_chats = true;
    } else {
        status.className = 'import-status error';
        status.innerHTML = `Unsupported file type. Please use .json or .zip exports.`;
        importedFile = null;
    }
}

async function uploadAndImport() {
    if (!importedFile) return true;

    const status = document.getElementById('import-status');
    const dropZone = document.getElementById('drop-zone');
    const step4Buttons = document.querySelector('#step-4 .btn-row');

    dropZone.style.display = 'none';
    status.style.display = 'block';
    status.className = 'import-status processing';
    status.innerHTML = `
        <div class="import-progress">
            <div class="spinner"></div>
            <div>
                <strong>Processing your chats...</strong>
                <p class="progress-hint">This may take a minute for large exports.</p>
            </div>
        </div>
    `;
    step4Buttons.querySelectorAll('button').forEach(b => b.disabled = true);

    try {
        const formData = new FormData();
        formData.append('file', importedFile);

        const response = await fetch('/api/import', {
            method: 'POST',
            body: formData,
        });
        const result = await response.json();

        if (!response.ok) throw new Error(result.error || 'Import failed');

        const convCount = (result.conversations || 0).toLocaleString();
        const msgCount = (result.messages || 0).toLocaleString();
        const factCount = result.facts_count || 0;
        const source = _formatSource(result.source);

        let summaryHTML = `
            <div class="import-summary">
                <div class="summary-header">
                    <span class="summary-icon">🧠</span>
                    <strong>Import Complete!</strong>
                </div>
                <div class="summary-stats">
                    <div class="stat">
                        <span class="stat-num">${convCount}</span>
                        <span class="stat-label">conversations</span>
                    </div>
                    <div class="stat">
                        <span class="stat-num">${msgCount}</span>
                        <span class="stat-label">messages</span>
                    </div>
                    <div class="stat">
                        <span class="stat-num">${factCount}</span>
                        <span class="stat-label">facts about you</span>
                    </div>
                </div>
                <p class="summary-source">Imported from ${source}</p>
        `;

        if (result.facts && result.facts.length > 0) {
            summaryHTML += `<div class="summary-facts"><p class="facts-label">Things I learned about you:</p><ul>`;
            const factsToShow = result.facts.slice(0, 5);
            for (const fact of factsToShow) {
                summaryHTML += `<li>${_escapeHtml(fact)}</li>`;
            }
            if (result.facts.length > 5) {
                summaryHTML += `<li class="facts-more">...and ${result.facts.length - 5} more</li>`;
            }
            summaryHTML += `</ul></div>`;
        }

        summaryHTML += `</div>`;
        status.className = 'import-status imported';
        status.innerHTML = summaryHTML;
        config.imported_chats = true;
        config.import_source = result.source;

    } catch (error) {
        console.error('Import error:', error);
        status.className = 'import-status error';
        status.innerHTML = `Import failed: ${_escapeHtml(error.message)}<br><button class="btn-outline btn-retry" onclick="retryImport()">Try again</button>`;
        dropZone.style.display = '';
    }

    step4Buttons.querySelectorAll('button').forEach(b => b.disabled = false);
    return true;
}

function retryImport() {
    const dropZone = document.getElementById('drop-zone');
    const status = document.getElementById('import-status');
    dropZone.style.display = '';
    status.style.display = 'none';
    importedFile = null;
}

function _formatSource(source) {
    const map = {
        'chatgpt': 'ChatGPT',
        'claude': 'Claude',
        'gemini_takeout': 'Google Gemini (Takeout)',
        'generic': 'chat export',
        'raw': 'text data',
    };
    return map[source] || source || 'your export';
}

function _escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

async function skipImport() {
    config.imported_chats = false;
    importedFile = null;
    await saveConfigAndStartEngine(); // Ensure config is saved even if step 3 save failed
    showStep(5);
    finish();
}

// ── Config Save (after step 3 — all essentials collected) ─────

let configSaved = false;

async function saveConfigAndStartEngine() {
    if (configSaved) return;
    config.setup_complete = true;

    try {
        const encoded = btoa(JSON.stringify(config));
        const response = await fetch(`/api/config?data=${encodeURIComponent(encoded)}`);
        if (!response.ok) throw new Error('Failed to save config');
        configSaved = true;
        console.log('Config saved + engine starting (after step 3)');
    } catch (error) {
        console.error('Error saving config:', error);
        localStorage.setItem('kiyomi-config', JSON.stringify(config));
    }
}

// ── Finish ─────────────────────────────────────────────────

async function finish() {
    // Save again to capture any import data from step 4
    config.setup_complete = true;
    try {
        const encoded = btoa(JSON.stringify(config));
        const response = await fetch(`/api/config?data=${encodeURIComponent(encoded)}`);
        if (!response.ok) throw new Error('Failed to save config');
        console.log('Final config saved');
    } catch (error) {
        console.error('Error saving final config:', error);
    }

    const loading = document.getElementById('done-loading');
    const ready = document.getElementById('done-ready');
    const subtitle = document.getElementById('done-subtitle');
    if (loading) loading.style.display = 'block';
    if (ready) ready.style.display = 'none';

    // Get bot username from Telegram API
    const token = config.telegram_token;
    let botUsername = '';

    if (token) {
        try {
            const resp = await fetch(`https://api.telegram.org/bot${token}/getMe`);
            const data = await resp.json();
            if (data.ok && data.result && data.result.username) {
                botUsername = data.result.username;
            }
        } catch (e) {
            console.log('Could not fetch bot info:', e);
        }
    }

    if (subtitle) subtitle.textContent = 'Starting your assistant...';

    // Poll for engine readiness (engine already started after step 3)
    let engineReady = false;
    for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 3000));
        try {
            if (token) {
                const resp = await fetch(`https://api.telegram.org/bot${token}/getMe`);
                const data = await resp.json();
                if (data.ok) { engineReady = true; break; }
            }
        } catch (e) {}

        if (i === 3 && subtitle) subtitle.textContent = 'Almost there — setting up your AI brain...';
    }

    if (loading) loading.style.display = 'none';
    if (ready) ready.style.display = 'block';
    if (subtitle) subtitle.textContent = "I'm running and ready to help!";

    const deepLink = document.getElementById('bot-deep-link');
    if (deepLink && botUsername) {
        deepLink.href = `https://t.me/${botUsername}`;
        deepLink.textContent = `Open @${botUsername} in Telegram →`;
    } else if (deepLink) {
        deepLink.href = 'https://telegram.org';
        deepLink.textContent = 'Open Telegram →';
    }
}

// Enter key support
document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        const activeStep = document.querySelector('.step.active');
        const btn = activeStep?.querySelector('.btn-primary');
        if (btn) btn.click();
    }
});

// Init
document.addEventListener('DOMContentLoaded', () => {
    showStep(1);
});
