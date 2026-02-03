/**
 * Kiyomi Onboarding Wizard
 * Simple. Beautiful. Gets the job done.
 */

let currentStep = 1;
let selectedProvider = 'gemini';
let importedFile = null;

// Generate a random bot username on page load
const randomSuffix = Math.random().toString(36).substr(2, 5);
const suggestedBotUsername = `kiyomi_${randomSuffix}_bot`;
document.addEventListener('DOMContentLoaded', () => {
    const el = document.getElementById('suggested-username');
    if (el) el.textContent = suggestedBotUsername;
});

// Copy text to clipboard (for BotFather steps)
function copyText(text, element) {
    // If it's the username, use the generated one
    if (text.startsWith('kiyomi_') && text.endsWith('_bot')) {
        text = suggestedBotUsername;
    }
    navigator.clipboard.writeText(text).then(() => {
        element.classList.add('copied');
        const hint = element.querySelector('.copy-hint');
        if (hint) {
            const orig = hint.textContent;
            hint.textContent = '✅ Copied!';
            setTimeout(() => {
                element.classList.remove('copied');
                hint.textContent = orig;
            }, 2000);
        }
    }).catch(() => {
        // Fallback for non-HTTPS
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
            hint.textContent = '✅ Copied!';
            setTimeout(() => {
                element.classList.remove('copied');
                hint.textContent = orig;
            }, 2000);
        }
    });
}

const config = {
    name: '',
    provider: 'gemini',
    gemini_key: '',
    anthropic_key: '',
    openai_key: '',
    telegram_token: '',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    imported_chats: false,
    setup_complete: false,
};

// Navigation
function showStep(step) {
    document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.dot').forEach(d => d.classList.remove('active'));
    
    const stepEl = document.getElementById(`step-${step}`);
    const dotEl = document.querySelector(`.dot[data-step="${step}"]`);
    
    if (stepEl) stepEl.classList.add('active');
    if (dotEl) dotEl.classList.add('active');
    
    currentStep = step;
    
    // Focus first input if exists
    setTimeout(() => {
        const input = stepEl?.querySelector('input:not([type="file"])');
        if (input) input.focus();
    }, 100);
}

async function nextStep(from) {
    if (!validateStep(from)) return;
    saveStepData(from);
    
    // Step 4: trigger upload before advancing
    if (from === 4 && importedFile) {
        await uploadAndImport();
    }
    
    showStep(from + 1);
    
    // Auto-trigger finish when entering Step 5
    if (from + 1 === 5) {
        finish();
    }
}

function prevStep(from) {
    showStep(from - 1);
}

// Validation
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
            const key = document.getElementById('api-key').value.trim();
            if (!key) {
                shake(document.getElementById('api-key'));
                return false;
            }
            return true;
        }
        case 3: {
            const token = document.getElementById('tg-token').value.trim();
            if (!token || !token.includes(':')) {
                shake(document.getElementById('tg-token'));
                return false;
            }
            return true;
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

// Add shake animation
const style = document.createElement('style');
style.textContent = `
    @keyframes shake {
        0%, 100% { transform: translateX(0); }
        25% { transform: translateX(-6px); }
        75% { transform: translateX(6px); }
    }
`;
document.head.appendChild(style);

// Save step data
function saveStepData(step) {
    switch (step) {
        case 1:
            config.name = document.getElementById('user-name').value.trim();
            document.getElementById('done-name').textContent = config.name;
            break;
        case 2: {
            const key = document.getElementById('api-key').value.trim();
            config.provider = selectedProvider;
            config[`${selectedProvider}_key`] = key;
            break;
        }
        case 3:
            config.telegram_token = document.getElementById('tg-token').value.trim();
            break;
    }
}

// Provider selection
function selectProvider(provider) {
    selectedProvider = provider;
    
    document.querySelectorAll('.provider-card').forEach(card => {
        card.classList.remove('selected');
    });
    document.getElementById(`prov-${provider}`).classList.add('selected');
    
    // Update help panel
    document.querySelectorAll('.help-content').forEach(el => {
        el.style.display = 'none';
    });
    const helpEl = document.getElementById(`help-${provider}`);
    if (helpEl) helpEl.style.display = 'block';
    
    // Clear API key field
    document.getElementById('api-key').value = '';
    document.getElementById('api-key').focus();
}

// Help toggle
function toggleHelp(event) {
    event.preventDefault();
    const panel = document.getElementById('help-panel');
    panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
    
    // Show correct provider help
    document.querySelectorAll('.help-content').forEach(el => {
        el.style.display = 'none';
    });
    const helpEl = document.getElementById(`help-${selectedProvider}`);
    if (helpEl) helpEl.style.display = 'block';
}

// File import
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
    if (files.length > 0) {
        processFile(files[0]);
    }
}

function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
        processFile(files[0]);
    }
}

function processFile(file) {
    importedFile = file;
    const status = document.getElementById('import-status');
    status.style.display = 'block';
    
    const sizeMB = (file.size / 1024 / 1024).toFixed(1);
    
    if (file.name.endsWith('.json') || file.name.endsWith('.zip')) {
        status.className = 'import-status success';
        status.innerHTML = `✅ <strong>${file.name}</strong> (${sizeMB}MB) — Ready to import!`;
        config.imported_chats = true;
    } else {
        status.className = 'import-status error';
        status.innerHTML = `❌ Unsupported file type. Please use .json or .zip exports.`;
        importedFile = null;
    }
}

async function uploadAndImport() {
    /**
     * Upload the selected file to /api/import, show progress,
     * then display a summary of what was imported.
     */
    if (!importedFile) return true; // Nothing to upload, allow proceed

    const status = document.getElementById('import-status');
    const dropZone = document.getElementById('drop-zone');
    const step4Buttons = document.querySelector('#step-4 .btn-row');

    // --- Phase 1: Uploading / Processing ---
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
    // Disable buttons while processing
    step4Buttons.querySelectorAll('button').forEach(b => b.disabled = true);

    try {
        const formData = new FormData();
        formData.append('file', importedFile);

        const response = await fetch('/api/import', {
            method: 'POST',
            body: formData,
        });

        const result = await response.json();

        if (!response.ok) {
            throw new Error(result.error || 'Import failed');
        }

        // --- Phase 2: Show summary ---
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

        // Show a few sample facts
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
        status.innerHTML = `❌ Import failed: ${_escapeHtml(error.message)}<br><button class="btn-outline btn-retry" onclick="retryImport()">Try again</button>`;
        // Re-show drop zone
        dropZone.style.display = '';
    }

    // Re-enable buttons
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

function skipImport() {
    config.imported_chats = false;
    importedFile = null;
    showStep(5);
    finish(); // Auto-trigger setup when entering final step
}

// Finish — called when Step 5 is shown
async function finish() {
    config.setup_complete = true;
    
    // Show loading state
    const loading = document.getElementById('done-loading');
    const ready = document.getElementById('done-ready');
    const subtitle = document.getElementById('done-subtitle');
    if (loading) loading.style.display = 'block';
    if (ready) ready.style.display = 'none';
    
    try {
        // Save config
        const encoded = btoa(JSON.stringify(config));
        const response = await fetch(`/api/config?data=${encodeURIComponent(encoded)}`);
        
        if (!response.ok) {
            throw new Error('Failed to save config');
        }
        
        console.log('Config saved successfully!');
    } catch (error) {
        console.error('Error saving config:', error);
        localStorage.setItem('kiyomi-config', JSON.stringify(config));
    }
    
    // Get bot username via Telegram API
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
    
    // Wait for engine to start (deps install + boot)
    if (subtitle) subtitle.textContent = 'Installing AI packages (first time only, about 30 seconds)...';
    
    // Poll for engine readiness (check every 5 seconds for up to 2 minutes)
    let engineReady = false;
    for (let i = 0; i < 24; i++) {
        await new Promise(r => setTimeout(r, 5000));
        try {
            if (token) {
                const resp = await fetch(`https://api.telegram.org/bot${token}/getMe`);
                const data = await resp.json();
                if (data.ok) {
                    engineReady = true;
                    break;
                }
            }
        } catch (e) {}
        
        // Update loading message
        if (i === 3 && subtitle) subtitle.textContent = 'Almost there — setting up your AI brain...';
        if (i === 8 && subtitle) subtitle.textContent = 'Still working — this only happens once...';
    }
    
    // Show ready state
    if (loading) loading.style.display = 'none';
    if (ready) ready.style.display = 'block';
    if (subtitle) subtitle.textContent = "I'm running and ready to help!";
    
    // Set up the deep link
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
