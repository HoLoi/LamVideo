const API_BASE = '';

// Detect file:// access — redirect user
if (location.protocol === 'file:') {
	document.body.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:sans-serif;padding:20px;">
      <div style="max-width:500px;text-align:center;">
        <h2 style="color:#b85c5c;">&#10007; Cannot open directly</h2>
        <p style="color:#6b5d4f;margin:16px 0;font-size:15px;line-height:1.6;">
          This app must be accessed through the backend server.
        </p>
        <ol style="text-align:left;color:#3d3529;font-size:14px;line-height:1.8;">
          <li>Run <code>run_api.bat</code> (or <code>run_api_cpu.bat</code>) — keep it running</li>
          <li>Open your browser to <strong><a href="http://localhost:8000">http://localhost:8000</a></strong></li>
        </ol>
      </div>
    </div>`;
	throw new Error('File protocol detected — use http://localhost:8000');
}

let STATE = 'idle';
let POLL_INTERVAL = null;
let CURRENT_TASK_ID = null;
let CURRENT_AUDIO_URL = null;
let CHUNK_DATA = [];
let GENERATION_PAUSED = false;
let GENERATION_START_TIME = null;
let CURRENT_FILTER = 'all';
let AUTOSAVE_TIMER = null;
let CHUNK_PAGE = 0;
let _expandedChunks = new Set();
const CHUNK_PER_PAGE = 10;
let CURRENT_VOICE_MODE = 'low';
let CURRENT_VOICE_ID = 'banmai';
let ALL_VOICES = { low: [], medium: [], high: [] };
let VOICE_RATES = { preset: 18, custom: {} };
let PAUSE_CFG = { enabled: true, pauses: { '.': 0.4, ',': 0.2, ';': 0.3, ':': 0.3, '?': 0.4, '!': 0.4, linebreak: 0.6 } };

const QUALITY_LABELS = { low: 'Piper', medium: 'F5', high: 'OmniVoice' };
const QUALITY_BADGE_CLASS = { low: 'piper', medium: 'f5', high: 'omnivoice' };

const textInput = document.getElementById('textInput');
const dropZone = document.getElementById('dropZone');
const fileInput = document.getElementById('fileInput');
const charCount = document.getElementById('charCount');
const wordCount = document.getElementById('wordCount');
const estDuration = document.getElementById('estDuration');
const estSegments = document.getElementById('estSegments');
const voiceModeToggle = document.getElementById('voiceModeToggle');
const previewBtn = document.getElementById('previewBtn');
const newVoiceBtn = document.getElementById('newVoiceBtn');
const generateBtn = document.getElementById('generateBtn');
const progressFloat = document.getElementById('progressFloat');
const floatFill = document.getElementById('floatFill');
const floatStatus = document.getElementById('floatStatus');
const floatCount = document.getElementById('floatCount');
const floatEta = document.getElementById('floatEta');
const floatCancelBtn = document.getElementById('floatCancelBtn');
const finalAudioBar = document.getElementById('finalAudioBar');
const finalAudioOverlay = document.getElementById('finalAudioOverlay');
const reopenFinalBtn = document.getElementById('reopenFinalBtn');
const audioPlayer = document.getElementById('audioPlayer');
const formatSelect = document.getElementById('formatSelect');
const downloadBtn = document.getElementById('downloadBtn');
const errorDisplay = document.getElementById('errorDisplay');
const chunkList = document.getElementById('chunkList');
const mergeSection = document.getElementById('mergeSection');
const mergeBtn = document.getElementById('mergeBtn');
const resetBtn = document.getElementById('resetBtn');
const chunkCount = document.getElementById('chunkCount');
const chunkPagination = document.getElementById('chunkPagination');
const pagePrev = document.getElementById('pagePrev');
const pageNext = document.getElementById('pageNext');
const pageInfo = document.getElementById('pageInfo');
const segmentFilters = document.getElementById('segmentFilters');
const batchActions = document.getElementById('batchActions');
const autosaveIndicator = document.getElementById('autosaveIndicator');
const autosaveText = document.getElementById('autosaveText');
let progressPopupActive = false;

function removeMiniPlayer() {
	const player = document.getElementById('chunkPlayer');
	if (!player) return;
	const audio = player.querySelector('audio');
	if (audio) audio.pause();
	player.remove();
}

function showProgressPopup() {
	progressPopupActive = true;
	removeMiniPlayer();
	hideFinalAudio();
	progressFloat.classList.add('visible');
}

function hideProgressPopup() {
	progressPopupActive = false;
	progressFloat.classList.remove('visible');
}

function prepareMiniPlayer() {
	progressFloat.classList.remove('visible');
	hideFinalAudio();
}

function closeMiniPlayer(player) {
	const audio = player.querySelector('audio');
	if (audio) audio.pause();
	player.remove();
	if (progressPopupActive) progressFloat.classList.add('visible');
}

function showFinalAudio() {
	removeMiniPlayer();
	hideProgressPopup();
	finalAudioBar.classList.add('visible');
	finalAudioOverlay.classList.add('visible');
	reopenFinalBtn.classList.remove('visible');
}

function hideFinalAudio() {
	finalAudioBar.classList.remove('visible');
	finalAudioOverlay.classList.remove('visible');
	if (CURRENT_AUDIO_URL) reopenFinalBtn.classList.add('visible');
}

// Slider fills
['speedSlider', 'volumeSlider'].forEach(id => {
	const el = document.getElementById(id);
	if (!el) return;
	const valEl = document.getElementById(id.replace('Slider', 'Val'));
	function updateSliderFill() {
		const min = parseFloat(el.min);
		const max = parseFloat(el.max);
		const val = parseFloat(el.value);
		const pct = ((val - min) / (max - min)) * 100;
		el.style.background = `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-primary) ${pct}%, var(--card-border) ${pct}%, var(--card-border) 100%)`;
	}
	el.addEventListener('input', function () {
		const v = this.value;
		if (id === 'speedSlider' && valEl) valEl.textContent = (Math.round(parseFloat(v) * 100) / 100).toFixed(2) + '×';
		else if (id === 'volumeSlider' && valEl) valEl.textContent = v + ' dB';
		else if (valEl) valEl.textContent = v;
		updateSliderFill();
	});
	updateSliderFill();
});

// Model config sliders
['cfgSlider', 'stepsSlider', 'swaySlider', 'numStepSlider'].forEach(id => {
	const el = document.getElementById(id);
	if (!el) return;
	const valEl = document.getElementById(id.replace('Slider', 'Val'));
	if (!valEl) return;
	el.addEventListener('input', function () {
		valEl.textContent = id === 'cfgSlider' || id === 'swaySlider' ? parseFloat(this.value).toFixed(1) : this.value;
		const min = parseFloat(el.min); const max = parseFloat(el.max);
		const pct = ((parseFloat(el.value) - min) / (max - min)) * 100;
		el.style.background = `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-primary) ${pct}%, var(--card-border) ${pct}%, var(--card-border) 100%)`;
	});
	const min = parseFloat(el.min); const max = parseFloat(el.max);
	const pct = ((parseFloat(el.value) - min) / (max - min)) * 100;
	el.style.background = `linear-gradient(to right, var(--accent-primary) 0%, var(--accent-primary) ${pct}%, var(--card-border) ${pct}%, var(--card-border) 100%)`;
});

// ─── Multi-file queue ───
let FILE_QUEUE = [];

let _fqSelected = null;
let _fqPage = 0;
const FQ_PER_PAGE = 7;

function _updateGenIndicator() {
	const sel = _fqSelected !== null ? FILE_QUEUE[_fqSelected] : null;
	document.getElementById('fileGenIndicator').textContent = sel ? 'File: ' + sel.name : '';
}

function _saveFileConfig(idx) {
	const f = FILE_QUEUE[idx];
	if (!f) return;
	const mode = document.querySelector('#voiceModeToggle .toggle-btn.active')?.dataset.mode || 'low';
	f.config = {
		voice_mode: mode,
		voice_id: CURRENT_VOICE_ID,
		voice_label: (ALL_VOICES[mode] || []).find(v => v.id === CURRENT_VOICE_ID)?.label || CURRENT_VOICE_ID,
		normalize: document.getElementById('normalizeCheckbox').checked,
		clean: document.getElementById('cleanCheckbox').checked,
		normalize_audio: document.getElementById('normalizeAudioCheckbox').checked,
		split_segments: true,
		split_mode: document.getElementById('splitModeSelect').value,
		speed: parseFloat(document.getElementById('speedSlider').value),
		volume: parseFloat(document.getElementById('volumeSlider').value),
	};
	f._configured = true;
	showToast('Config saved for ' + f.name);
	renderFileQueue();
}

function renderFileQueue() {
	const container = document.getElementById('fileQueue');
	if (FILE_QUEUE.length === 0) { container.style.display = 'none'; _updateGenIndicator(); return; }
	container.style.display = '';
	_updateGenIndicator();
	const hasPending = FILE_QUEUE.some(f => f.status === 'pending');
	const totalPages = Math.max(1, Math.ceil(FILE_QUEUE.length / FQ_PER_PAGE));
	if (_fqPage >= totalPages) _fqPage = totalPages - 1;
	const start = _fqPage * FQ_PER_PAGE;
	const pageItems = FILE_QUEUE.slice(start, start + FQ_PER_PAGE);
	container.innerHTML = `
          <div class="fq-header">
            <span>${FILE_QUEUE.length} / ${MAX_QUEUE} files${totalPages > 1 ? ' · Page ' + (_fqPage + 1) + '/' + totalPages : ''}</span>
            <div style="display:flex;gap:4px;flex-wrap:wrap;">
              ${hasPending ? `<button onclick="for(let i=0;i<FILE_QUEUE.length;i++){if(FILE_QUEUE[i].status==='pending')_saveFileConfig(i)}renderFileQueue();showToast('Config applied to all pending files')" style="background:var(--bg-secondary);color:var(--text-secondary);border-color:var(--card-border);font-size:11px;">Apply to all</button>` : ''}
              ${hasPending ? `<button onclick="processAllFiles()" id="processAllBtn">Process All</button>` : ''}
              ${totalPages > 1 && _fqPage > 0 ? `<button onclick="_fqPage--;renderFileQueue()" style="background:var(--bg-secondary);color:var(--text-secondary);border-color:var(--card-border);font-size:11px;">&#9664;</button>` : ''}
              ${totalPages > 1 && _fqPage < totalPages - 1 ? `<button onclick="_fqPage++;renderFileQueue()" style="background:var(--bg-secondary);color:var(--text-secondary);border-color:var(--card-border);font-size:11px;">&#9654;</button>` : ''}
              <button onclick="FILE_QUEUE=[];_fqSelected=null;renderFileQueue()" style="background:var(--bg-secondary);color:var(--text-secondary);border-color:var(--card-border);">Clear</button>
            </div>
          </div>
          ${pageItems.map((f, i) => {
		const fi = start + i;
		const isSel = _fqSelected === fi;
		const statusLabel = f.status === 'done' ? 'Done' : f.status === 'processing' ? 'Processing...' : f.status === 'error' ? 'Error' : 'Pending';
		const segCount = f.chunks ? f.chunks.filter(c => c.status === 'done').length : 0;
		const segTotal = f.chunks ? f.chunks.length : 0;
		const cfg = f.config;
		const cfgSummary = cfg ? `${cfg.voice_label || cfg.voice_id} ${cfg.split_mode || 'default'}` : '';
		return `<div class="file-queue-item" style="cursor:pointer;flex-wrap:wrap;${isSel ? 'border-color:var(--accent-primary);background:rgba(91,122,106,0.04);' : ''}" onclick="if(event.target.closest('button'))return;_fqSelected=_fqSelected===${fi}?null:${fi};_loadFileToEditor(${fi});renderFileQueue()">
              <span class="fq-name" title="${f.name}">${escapeHtml(f.name)}</span>
              <span style="font-size:11px;color:var(--text-muted);font-family:monospace;">
                ${cfgSummary ? cfgSummary + ' ' : ''}${segTotal > 0 ? segCount + '/' + segTotal + ' seg' : ''}${f.duration ? ' ' + formatDuration(f.duration) : ''}
              </span>
              <span class="fq-status ${f.status}">${statusLabel}</span>
              <span class="fq-actions">
                ${f.status === 'pending' ? `<button onclick="event.stopPropagation();_saveFileConfig(${fi})" title="Save config" style="border:none;background:none;cursor:pointer;font-size:14px;color:${f._configured ? 'var(--success)' : 'var(--text-muted)'};">${f._configured ? '\u2713' : '\u2699'}</button>` : ''}
                <button onclick="event.stopPropagation();FILE_QUEUE.splice(${fi},1);_fqSelected=_fqSelected===${fi}?null:_fqSelected;renderFileQueue()" style="color:var(--error);border:none;background:none;cursor:pointer;font-size:14px;">&#10005;</button>
              </span>
            </div>`;
	}).join('')}`;
}

function _loadFileToEditor(idx) {
	if (idx < 0 || idx >= FILE_QUEUE.length) {
		CHUNK_DATA = []; CURRENT_TASK_ID = null; CURRENT_AUDIO_URL = null;
		renderChunks([]); hideFinalAudio(); return;
	}
	const f = FILE_QUEUE[idx];
	if (!f || !f.text) return;
	textInput.value = f.text;
	textInput.dispatchEvent(new Event('input'));
	if (f.config) {
		const c = f.config;
		const mode = c.voice_mode || document.querySelector('#voiceModeToggle .toggle-btn.active')?.dataset.mode || 'low';
		document.querySelectorAll('#voiceModeToggle .toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
		CURRENT_VOICE_MODE = mode;
		if (c.voice_id) CURRENT_VOICE_ID = c.voice_id;
		updateVoiceLabel();
		updateSplitCheckbox();
		updateModelConfig();
		document.getElementById('normalizeCheckbox').checked = c.normalize || false;
		document.getElementById('cleanCheckbox').checked = c.clean || false;
		document.getElementById('normalizeAudioCheckbox').checked = c.normalize_audio !== false;
		if (c.split_mode) document.getElementById('splitModeSelect').value = c.split_mode;
		if (c.speed !== undefined) { document.getElementById('speedSlider').value = c.speed; document.getElementById('speedSlider').dispatchEvent(new Event('input')); }
		if (c.volume !== undefined) { document.getElementById('volumeSlider').value = c.volume; document.getElementById('volumeSlider').dispatchEvent(new Event('input')); }
	}
	// Load results into main UI if file is done
	if (f.status === 'done' && f.chunks && f.chunks.length > 0) {
		CHUNK_DATA = f.chunks;
		CURRENT_TASK_ID = f.task_id || ('_file_' + idx);
		CURRENT_AUDIO_URL = f.audio_url || null;
		renderChunks(CHUNK_DATA);
		if (f.audio_url) {
			CURRENT_AUDIO_URL = f.audio_url;
			loadAudio(`${API_BASE}${f.audio_url}`);
			showFinalAudio();
		}
		if (f.chunks.length > 1) mergeSection.classList.remove('hidden');
		else mergeSection.classList.add('hidden');
	} else {
		CHUNK_DATA = [];
		CURRENT_TASK_ID = null;
		CURRENT_AUDIO_URL = null;
		renderChunks([]);
		mergeSection.classList.add('hidden');
		hideFinalAudio();
	}
}

const MAX_QUEUE = 20;

function addFilesToQueue(files) {
	if (FILE_QUEUE.length >= MAX_QUEUE) {
		showToast('Max ' + MAX_QUEUE + ' files allowed'); return;
	}
	let added = 0;
	for (const file of files) {
		if (!file.name.endsWith('.txt') && !file.name.endsWith('.md')) continue;
		if (file.size === 0) { showToast('Skipped empty: ' + file.name); continue; }
		const name = file.name.replace(/\.(txt|md)$/i, '');
		if (FILE_QUEUE.some(f => f.name === name)) continue;
		if (FILE_QUEUE.length >= MAX_QUEUE) { showToast('Queue full, skipped remaining'); break; }
		const idx = FILE_QUEUE.length;
		FILE_QUEUE.push({ name, file, text: '', status: 'pending', task_id: null, audio_url: null, duration: null });
		const reader = new FileReader();
		reader.onload = function () {
			const txt = (this.result || '').trim();
			if (!txt) {
				FILE_QUEUE.splice(idx, 1);
				showToast('Removed empty: ' + name);
			} else {
				FILE_QUEUE[idx].text = txt;
			}
			renderFileQueue();
		};
		reader.readAsText(file);
		added++;
	}
	renderFileQueue();
	if (added > 0) showToast('Added ' + added + ' file' + (added > 1 ? 's' : ''));
}

async function processSingleFile(idx) {
	const f = FILE_QUEUE[idx];
	if (!f || f.status !== 'pending' || !f.text) return;
	f.status = 'processing'; renderFileQueue();
	try {
		const c = f.config || {};
		const mode = c.voice_mode || document.querySelector('#voiceModeToggle .toggle-btn.active')?.dataset.mode || 'low';
		const vid = c.voice_id || CURRENT_VOICE_ID || 'banmai';
		f.voice = (ALL_VOICES[mode] || []).find(v => v.id === vid)?.label || vid;
		const res = await fetch(`${API_BASE}/tts/generate`, {
			method: 'POST', headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				text: f.text, voice_mode: mode, voice_id: vid, output_format: 'mp3',
				normalize: c.normalize !== undefined ? c.normalize : document.getElementById('normalizeCheckbox').checked,
				clean: c.clean !== undefined ? c.clean : document.getElementById('cleanCheckbox').checked,
				normalize_audio: c.normalize_audio !== undefined ? c.normalize_audio : document.getElementById('normalizeAudioCheckbox').checked,
				speed: c.speed !== undefined ? c.speed : parseFloat(document.getElementById('speedSlider').value),
				volume: c.volume !== undefined ? c.volume : parseFloat(document.getElementById('volumeSlider').value),
				pitch: 0,
				split_segments: true,
				split_mode: c.split_mode || document.getElementById('splitModeSelect').value,
				cfg_strength: parseFloat(document.getElementById('cfgSlider').value),
				steps: parseInt(document.getElementById('stepsSlider').value),
				sway: parseFloat(document.getElementById('swaySlider').value),
				num_step: parseInt(document.getElementById('numStepSlider').value),
			}),
		});
		if (!res.ok) throw new Error('Generation failed');
		const data = await res.json();
		f.task_id = data.task_id;
		for (let poll = 0; poll < 120; poll++) {
			await new Promise(r => setTimeout(r, 1000));
			const sr = await fetch(`${API_BASE}/tts/status/${data.task_id}`);
			if (!sr.ok) throw new Error('Status failed');
			const sd = await sr.json();
			if (sd.status === 'chunks_done' || sd.status === 'done') {
				f.chunks = sd.chunks || [];
				if (sd.audio_url) {
					f.audio_url = sd.audio_url;
				} else if (f.chunks.length > 1) {
					// Auto-merge multi-segment
					try {
						const m = await fetch(`${API_BASE}/tts/merge`, {
							method: 'POST', headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({ task_id: data.task_id, output_format: 'mp3' }),
						});
						if (m.ok) { const md = await m.json(); f.audio_url = md.audio_url; f.duration = md.duration; }
					} catch (_) { }
				}
				if (!f.audio_url && f.chunks.length > 0) {
					const done = f.chunks.find(c => c.audio_url);
					if (done) f.audio_url = done.audio_url;
				}
				f.duration = sd.duration || f.duration || null;
				f.status = 'done';
				showToast(`Done: ${f.name}`);
				break;
			} else if (sd.status === 'error') {
				throw new Error(sd.error || 'Generation error');
			}
		}
		if (f.status === 'processing') { f.status = 'error'; f.error = 'Timeout'; }
	} catch (e) {
		f.status = 'error'; f.error = e.message;
		showToast(`Failed: ${f.name} — ${e.message}`);
	}
	_fqSelected = idx;
	renderFileQueue();
	_loadFileToEditor(idx);
}

async function processAllFiles() {
	const pending = FILE_QUEUE.map((f, i) => i).filter(i => FILE_QUEUE[i].status === 'pending');
	if (pending.length === 0) return;
	document.getElementById('processAllBtn').disabled = true;
	for (const idx of pending) {
		if (FILE_QUEUE[idx].status !== 'pending') continue;
		await processSingleFile(idx);
	}
	renderFileQueue();
}

// Drop zone handling
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', e => {
	e.preventDefault();
	dropZone.classList.remove('dragover');
	const files = Array.from(e.dataTransfer.files).filter(f => f.name.endsWith('.txt') || f.name.endsWith('.md'));
	if (files.length === 0) return;
	if (files.length === 1 && FILE_QUEUE.length === 0) {
		// Single file — load into textarea (backward compatible)
		const reader = new FileReader();
		reader.onload = ev => { textInput.value = ev.target.result; textInput.dispatchEvent(new Event('input')); showToast(`Loaded ${files[0].name}`); };
		reader.readAsText(files[0]);
	} else {
		addFilesToQueue(files);
	}
});
dropZone.addEventListener('click', e => {
	if (e.target === dropZone || e.target.closest('.drop-zone-hint')) {
		fileInput.click();
	}
});
fileInput.addEventListener('change', e => {
	const files = Array.from(e.target.files).filter(f => f.name.endsWith('.txt') || f.name.endsWith('.md'));
	if (files.length > 0) addFilesToQueue(files);
	e.target.value = '';
});

// Text input handling
textInput.addEventListener('input', () => {
	updateCharCount();
	if (textInput.value.trim()) {
		dropZone.classList.remove('empty');
	} else {
		dropZone.classList.add('empty');
	}
	clearTimeout(AUTOSAVE_TIMER);
	AUTOSAVE_TIMER = setTimeout(() => {
		localStorage.setItem('tts_draft', textInput.value);
		updateAutosaveIndicator();
	}, 2000);
});

// Load draft on startup
const savedDraft = localStorage.getItem('tts_draft');
if (savedDraft) {
	textInput.value = savedDraft;
	dropZone.classList.remove('empty');
	textInput.dispatchEvent(new Event('input'));
}

// Input action buttons
document.getElementById('clearTextBtn').addEventListener('click', () => {
	if (textInput.value && !confirm('Clear all text?')) return;
	textInput.value = '';
	textInput.dispatchEvent(new Event('input'));
});

document.getElementById('saveDraftBtn').addEventListener('click', () => {
	localStorage.setItem('tts_draft', textInput.value);
	showToast('Draft saved');
	updateAutosaveIndicator();
});

function updateAutosaveIndicator() {
	autosaveIndicator.style.display = 'flex';
	const now = new Date();
	autosaveText.textContent = `Saved ${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;
}

voiceModeToggle.addEventListener('click', e => {
	const btn = e.target.closest('.toggle-btn');
	if (!btn) return;
	const mode = btn.dataset.mode;
	const list = ALL_VOICES[mode] || [];
	if (list.length === 0) {
		const names = { medium: 'F5-TTS', high: 'OmniVoice' };
		showToast(`Load ${names[mode] || mode} model in Resources tab first`);
		return;
	}
	voiceModeToggle.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
	btn.classList.add('active');
	CURRENT_VOICE_MODE = mode;
	document.getElementById('newVoiceBtn').style.display = (CURRENT_VOICE_MODE === 'medium' || CURRENT_VOICE_MODE === 'high') ? 'inline-block' : 'none';
	updateSplitCheckbox();
	updateModelConfig();
	if (!list.find(v => v.id === CURRENT_VOICE_ID)) {
		selectVoice(list[0].id, list[0].label, list[0].gender || '');
	}
	updateVoiceLabel();
	updateCharCount();
});

async function loadVoices() {
	try {
		const res = await fetch(`${API_BASE}/tts/voices`);
		const data = await res.json();
		ALL_VOICES = data;

		// Hide quality tiers with no loaded voices
		['low', 'medium', 'high'].forEach(mode => {
			const hasVoices = (data[mode] && data[mode].length > 0);
			const toggleBtn = document.querySelector(`#voiceModeToggle .toggle-btn[data-mode="${mode}"]`);
			const modalTab = document.querySelector(`#voiceModal .dict-tab[data-tab="${mode}"]`);
			if (toggleBtn) toggleBtn.style.display = hasVoices ? '' : 'none';
			if (modalTab) modalTab.style.display = hasVoices ? '' : 'none';
		});

		// If no voices at all, show a "download needed" message and disable actions
		if (!anyVoiceExists()) {
			document.getElementById('currentVoiceLabel').innerHTML =
				'<span style="color:var(--accent-gold);font-size:13px;">&#9888; Please download/select a voice first</span>';
			document.querySelectorAll('#voiceModeToggle .toggle-btn').forEach(b => b.style.display = '');
			setVoiceAvailability(false);
			return;
		}
		setVoiceAvailability(true);

		checkGpuAndShowInfo();

		// If current mode has no voices, pick first available
		if (!(ALL_VOICES[CURRENT_VOICE_MODE] && ALL_VOICES[CURRENT_VOICE_MODE].length > 0)) {
			const available = ['low', 'medium', 'high'].find(m => ALL_VOICES[m] && ALL_VOICES[m].length > 0);
			if (available) {
				CURRENT_VOICE_MODE = available;
				document.querySelectorAll('#voiceModeToggle .toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === available));
			}
		}

		// Activate first visible toggle if none is active
		if (!document.querySelector('#voiceModeToggle .toggle-btn.active')) {
			const first = document.querySelector('#voiceModeToggle .toggle-btn');
			if (first) { first.classList.add('active'); CURRENT_VOICE_MODE = first.dataset.mode; }
		}

		if (data.low && data.low.length && data.low[0].rate) VOICE_RATES.preset = data.low[0].rate;
		const cr = {};
		(data.medium || []).forEach(v => { cr[v.id] = v.rate || 18; });
		(data.high || []).forEach(v => { cr[v.id] = v.rate || 8; });
		VOICE_RATES.custom = cr;
		if (ALL_VOICES[CURRENT_VOICE_MODE] && ALL_VOICES[CURRENT_VOICE_MODE].length > 0) {
			const list = ALL_VOICES[CURRENT_VOICE_MODE];
			if (!list.find(v => v.id === CURRENT_VOICE_ID)) {
				const first = list[0];
				selectVoice(first.id, first.label, first.gender || '');
			}
		}
	} catch (e) { console.error(e); }
}

function setVoiceAvailability(available) {
	const gen = document.getElementById('generateBtn');
	const prev = document.getElementById('previewBtn');
	if (gen) gen.disabled = !available;
	if (prev) prev.disabled = !available;
}

function selectVoice(id, label, gender) {
	CURRENT_VOICE_ID = id;
	updateVoiceLabel();
	updateSplitCheckbox();
	updateModelConfig();
}

async function checkGpuAndShowInfo() {
	try {
		const res = await fetch(`${API_BASE}/tts/model_status`);
		const data = await res.json();
		const gpu = data.gpu || {};
		const badge = document.getElementById('gpuBadge');
		if (gpu.available) {
			const labels = { low: 'CPU', medium: 'GPU', high: 'GPU' };
			const rec = (data.recommended_quality || ['low']).map(q => labels[q] || q).join('/');
			badge.innerHTML = `<span style="color:var(--accent-primary);">&#9679;</span> ${gpu.name} (${gpu.vram_gb}GB) · <span style="color:var(--text-primary);">${rec}</span>`;
			badge.style.display = 'flex';
		} else {
			badge.innerHTML = `<span style="color:var(--text-muted);">&#9679;</span> No GPU · CPU`;
			badge.style.display = 'flex';
		}
	} catch (e) {
		// silently ignore
	}
}

function anyVoiceExists() {
	return ['low', 'medium', 'high'].some(m => ALL_VOICES[m] && ALL_VOICES[m].length > 0);
}

function updateVoiceLabel() {
	if (!anyVoiceExists()) {
		document.getElementById('currentVoiceLabel').innerHTML =
			'<span style="color:var(--accent-gold);font-size:13px;">&#9888; Go to Resources to download voices</span>';
		return;
	}
	const list = ALL_VOICES[CURRENT_VOICE_MODE] || [];
	const v = list.find(x => x.id === CURRENT_VOICE_ID);
	const genderIcon = v && v.gender === 'male' ? '&#9794;' : v && v.gender === 'female' ? '&#9792;' : '';
	const badgeClass = QUALITY_BADGE_CLASS[CURRENT_VOICE_MODE] || 'piper';
	const engineBadge = `<span class="voice-engine-badge ${badgeClass}">${QUALITY_LABELS[CURRENT_VOICE_MODE]}</span>`;
	const label = v ? `${v.label} <span class="gender-icon">${genderIcon}</span>${engineBadge}` : (CURRENT_VOICE_ID || 'Unknown');
	document.getElementById('currentVoiceLabel').innerHTML = label;
}

function updateSplitCheckbox() {
	document.getElementById('splitModeOptions').style.display = '';
}

function updateModelConfig() {
	const panel = document.getElementById('modelConfig');
	if (!panel) return;
	const cfgRow = document.getElementById('cfgRow');
	const stepsRow = document.getElementById('stepsRow');
	const swayRow = document.getElementById('swayRow');
	const numStepRow = document.getElementById('numStepRow');
	const title = panel.querySelector('div');
	if (CURRENT_VOICE_MODE === 'low') {
		panel.classList.add('hidden');
	} else {
		panel.classList.remove('hidden');
		if (cfgRow) cfgRow.style.display = '';
		if (CURRENT_VOICE_MODE === 'medium') {
			if (title) title.textContent = 'Model Config \u2014 Medium (F5)';
			if (stepsRow) stepsRow.style.display = '';
			if (swayRow) swayRow.style.display = '';
			if (numStepRow) numStepRow.style.display = 'none';
		} else {
			if (title) title.textContent = 'Model Config \u2014 High (OmniVoice)';
			if (stepsRow) stepsRow.style.display = 'none';
			if (swayRow) swayRow.style.display = 'none';
			if (numStepRow) numStepRow.style.display = '';
		}
	}
}

function getCurrentVoiceLabel() {
	const list = ALL_VOICES[CURRENT_VOICE_MODE] || [];
	const v = list.find(x => x.id === CURRENT_VOICE_ID);
	return v ? v.label : CURRENT_VOICE_ID;
}

function updateCharCount() {
	const text = textInput.value;
	const len = text.length;
	const words = len > 0 ? text.split(/\s+/).filter(w => w.length > 0).length : 0;

	charCount.textContent = `${len.toLocaleString()} chars`;
	charCount.style.color = len > 4800 ? 'var(--error)' : 'var(--text-muted)';

	if (wordCount) wordCount.textContent = `${words.toLocaleString()} words`;

	let estSec = len > 0 ? len / VOICE_RATES.preset : 0;
	if (PAUSE_CFG.enabled) {
		const p = PAUSE_CFG.pauses;
		for (const ch of Object.keys(p)) {
			if (ch === 'linebreak') continue;
			const count = (text.match(new RegExp('\\' + ch, 'g')) || []).length;
			estSec += count * p[ch];
		}
		const lb = (text.match(/\n\s*\n/g) || []).length;
		estSec += lb * (p.linebreak || 0);
		const markers = text.match(/\[(\d+(?:\.\d+)?)\s*s\]/gi) || [];
		for (const m of markers) {
			const val = parseFloat(m.match(/[\d.]+/)[0]);
			estSec += val;
		}
	}

	const rounded = Math.round(estSec);
	const estMin = Math.floor(rounded / 60);
	const estRem = rounded % 60;
	const durationStr = estMin > 0 ? `~${estMin}m ${estRem}s` : `~${estRem}s`;

	if (estDuration) estDuration.textContent = `${durationStr} audio`;

	const estSegCount = len > 0 ? Math.ceil(len / 400) : 1;
	if (estSegments) estSegments.textContent = `${estSegCount} segment${estSegCount !== 1 ? 's' : ''}`;
}

/* Voice Modal */
function switchVoiceTab(tab) {
	document.querySelectorAll('#voiceModal .dict-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
	document.getElementById('voiceLowList').classList.toggle('hidden', tab !== 'low');
	document.getElementById('voiceMediumList').classList.toggle('hidden', tab !== 'medium');
	document.getElementById('voiceHighList').classList.toggle('hidden', tab !== 'high');
	renderCurrentVoiceTab();
}

function renderCurrentVoiceTab() {
	const active = document.querySelector('#voiceModal .dict-tab.active');
	renderVoiceList(active ? active.dataset.tab : CURRENT_VOICE_MODE);
}

function renderVoiceList(mode) {
	const containerId = mode === 'low' ? 'voiceLowList' : mode === 'medium' ? 'voiceMediumList' : 'voiceHighList';
	const container = document.getElementById(containerId);
	const genderFilter = document.getElementById('voiceGenderFilter')?.value || '';
	const typeFilter = document.getElementById('voiceTypeFilter')?.value || '';
	let list = ALL_VOICES[mode] || [];
	if (genderFilter) list = list.filter(v => v.gender === genderFilter);
	if (typeFilter === 'clone') list = list.filter(v => v.is_clone);
	else if (typeFilter === 'default') list = list.filter(v => !v.is_clone);
	if (list.length === 0) {
		container.innerHTML = '<div class="hist-empty">No voices match the filter</div>'; return;
	}
	container.innerHTML = list.map(v => {
		const isSelected = v.id === CURRENT_VOICE_ID && CURRENT_VOICE_MODE === mode;
		const genderIcon = v.gender === 'male' ? '&#9794;' : v.gender === 'female' ? '&#9792;' : '&#9733;';
		const genderColor = v.gender === 'male' ? 'var(--accent-primary)' : v.gender === 'female' ? '#c47a9e' : 'var(--text-muted)';
		const cloneBadge = v.is_clone ? '<span style="font-size:10px;background:var(--accent-gold);color:#fff;padding:1px 6px;border-radius:8px;margin-left:6px;font-weight:600;">CLONE</span>' : '<span style="font-size:10px;background:var(--bg-secondary);color:var(--text-muted);padding:1px 6px;border-radius:8px;margin-left:6px;">DEFAULT</span>';
		return `<div class="voice-card ${isSelected ? 'selected' : ''}" onclick="selectVoiceFromCard('${mode}','${v.id}')">
      <div class="voice-card-avatar" style="background:${genderColor}15;color:${genderColor};border-color:${genderColor}40;">${genderIcon}</div>
      <div class="voice-card-info">
        <div class="voice-card-name">${escapeHtml(v.label)}${cloneBadge}</div>
        <div class="voice-card-desc" title="${escapeHtml(v.description || v.ref_text || '')}">${escapeHtml(v.description || v.ref_text || '')}</div>
      </div>
      <div class="voice-card-actions" style="display:flex;gap:4px;align-items:center;">
        <button onclick="event.stopPropagation();previewVoice('${mode}','${v.id}')" title="Preview">
          <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        </button>
        ${v.is_clone ? `<button onclick="event.stopPropagation();editVoiceDesc('${mode}','${v.id}')" title="Edit description">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
        </button>` : ''}
        ${v.is_clone ? `<button onclick="event.stopPropagation();deleteVoice('${mode}','${v.id}')" title="Delete voice" style="color:var(--error);">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>` : ''}
      </div>
    </div>`;
	}).join('');
}

function selectVoiceFromCard(mode, id) {
	const list = ALL_VOICES[mode] || [];
	const v = list.find(x => x.id === id);
	if (v) {
		CURRENT_VOICE_MODE = mode;
		CURRENT_VOICE_ID = id;
		updateVoiceLabel();
		updateSplitCheckbox();
		updateModelConfig();
		document.querySelectorAll('#voiceModeToggle .toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mode));
		document.getElementById('newVoiceBtn').style.display = (mode === 'medium' || mode === 'high') ? 'inline-block' : 'none';
		updateCharCount();
		renderVoiceList(mode);
	}
}

function editVoiceDesc(mode, id) {
	const list = ALL_VOICES[mode] || [];
	const v = list.find(x => x.id === id);
	if (!v) return;
	const newDesc = prompt('Edit description:', v.description || '');
	if (newDesc === null) return;
	fetch(`${API_BASE}/tts/voices/${id}`, {
		method: 'PATCH', headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ description: newDesc.trim() || 'No description' }),
	}).then(r => { if (!r.ok) throw new Error('Failed'); return r.json(); })
		.then(() => { loadVoices().then(() => renderCurrentVoiceTab()); showToast('Description updated'); })
		.catch(() => alert('Failed to update description'));
}

function deleteVoice(mode, id) {
	if (!confirm('Delete this cloned voice?')) return;
	fetch(`${API_BASE}/tts/voices/${id}`, { method: 'DELETE' })
		.then(r => { if (!r.ok) throw new Error('Failed'); return r.json(); })
		.then(() => {
			loadVoices().then(() => {
				renderCurrentVoiceTab();
				if (CURRENT_VOICE_ID === id) {
					const first = Object.values(ALL_VOICES).flat()[0];
					if (first) selectVoiceFromCard(first.engine === 'f5' ? 'medium' : first.engine === 'omnivoice' ? 'high' : first.engine || 'low', first.id);
				}
			});
			showToast('Voice deleted');
		})
		.catch(() => alert('Cannot delete default voice'));
}

function previewVoice(mode, id) {
	const list = ALL_VOICES[mode] || [];
	const v = list.find(x => x.id === id);
	if (!v) return;
	if (mode === 'low') {
		const text = 'Xin chào đây là đoạn thử giọng nói, cảm ơn bạn đã lắng nghe.';
		fetch(`${API_BASE}/tts/preview`, {
			method: 'POST', headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ text, voice_mode: 'low', voice_id: id, normalize: false, clean: false, normalize_audio: true, speed: 1, pitch: 0, volume: 0 }),
		}).then(async r => {
			const d = await r.json().catch(() => ({}));
			if (!r.ok) { alert(d.detail || 'Preview failed'); return null; }
			return d;
		}).then(data => {
			if (!data) return;
			const wrap = getOrCreateMiniPlayer();
			const aud = wrap.querySelector('audio');
			aud.src = `${API_BASE}${data.audio_url}`;
			aud.play().catch(() => { });
		}).catch(() => { });
	} else {
		const engine = mode === 'medium' ? 'f5' : 'omnivoice';
		const url = `${API_BASE}/tts/voice_audio/${engine}/${id}`;
		const wrap = getOrCreateMiniPlayer();
		const aud = wrap.querySelector('audio');
		aud.src = url;
		aud.play().catch(() => { });
	}
}

function getOrCreateMiniPlayer() {
	prepareMiniPlayer();
	const old = document.getElementById('chunkPlayer');
	if (old) return old;
	const wrap = document.createElement('div');
	wrap.id = 'chunkPlayer';
	wrap.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-md);padding:10px 18px;box-shadow:var(--shadow-lg);z-index:300;display:flex;align-items:center;gap:12px;';
	const aud = document.createElement('audio');
	aud.controls = true;
	aud.style.cssText = 'height:34px;width:280px;';
	const close = document.createElement('button');
	close.innerHTML = '&times;';
	close.style.cssText = 'border:none;background:none;font-size:22px;cursor:pointer;color:var(--text-muted);padding:0 4px;line-height:1;';
	close.onclick = () => closeMiniPlayer(wrap);
	wrap.appendChild(aud);
	wrap.appendChild(close);
	document.body.appendChild(wrap);
	return wrap;
}

document.getElementById('selectVoiceBtn').addEventListener('click', () => {
	const modal = document.getElementById('voiceModal');
	modal.classList.remove('hidden');
	document.getElementById('voiceGenderFilter').value = '';
	document.getElementById('voiceTypeFilter').value = '';
	switchVoiceTab(CURRENT_VOICE_MODE);
});
document.getElementById('voiceCloseBtn').addEventListener('click', () => {
	document.getElementById('voiceModal').classList.add('hidden');
});
document.getElementById('voiceModal').addEventListener('click', e => {
	if (e.target === e.currentTarget) document.getElementById('voiceModal').classList.add('hidden');
});

document.getElementById('insertPauseBtn').addEventListener('click', () => {
	const ta = document.getElementById('textInput');
	const sel = document.getElementById('pauseDurSelect');
	const marker = `[${sel.value}s]`;
	const start = ta.selectionStart, end = ta.selectionEnd;
	const pos = end;
	ta.value = ta.value.slice(0, pos) + marker + ta.value.slice(pos);
	const newPos = pos + marker.length;
	ta.setSelectionRange(newPos, newPos);
	ta.focus();
	ta.dispatchEvent(new Event('input'));
});

previewBtn.addEventListener('click', async () => {
	const text = textInput.value.trim();
	if (!text) return;
	if (!anyVoiceExists()) { showError('Please download/select a voice first'); return; }
	const normalize = document.getElementById('normalizeCheckbox').checked;
	previewBtn.disabled = true; previewBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px;animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke-dasharray="30 70"/></svg> Preview';
	try {
		const res = await fetch(`${API_BASE}/tts/preview`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ text, voice_mode: CURRENT_VOICE_MODE, voice_id: CURRENT_VOICE_ID, normalize, clean: document.getElementById('cleanCheckbox').checked, normalize_audio: document.getElementById('normalizeAudioCheckbox').checked, speed: parseFloat(document.getElementById('speedSlider').value), pitch: 0, volume: parseFloat(document.getElementById('volumeSlider').value), cfg_strength: parseFloat(document.getElementById('cfgSlider').value), steps: parseInt(document.getElementById('stepsSlider').value), sway: parseFloat(document.getElementById('swaySlider').value), num_step: parseInt(document.getElementById('numStepSlider').value) }),
		});
		if (!res.ok) {
			const d = await res.json().catch(() => ({}));
			throw new Error(d.detail || ('Preview failed (' + res.status + ')'));
		}
		const data = await res.json();
		loadAudio(`${API_BASE}${data.audio_url}`);
		audioPlayer.play().catch(() => { });
	} catch (e) { showError('Preview: ' + e.message); }
	previewBtn.disabled = false; previewBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor" style="width:16px;height:16px;"><polygon points="5 3 19 12 5 21 5 3"/></svg> Preview';
});

generateBtn.addEventListener('click', startGeneration);

async function startGeneration() {
	if (!anyVoiceExists()) { showError('Please download/select a voice first'); return; }
	const selFile = _fqSelected !== null ? FILE_QUEUE[_fqSelected] : null;
	if (selFile) {
		// File mode: use file's stored text (ignore textarea edits)
		if (!selFile.text) { showError('File has no text'); return; }
		// Save current UI settings as file config before generating
		_saveFileConfig(_fqSelected);
	} else {
		// Normal mode: use textarea
		const text = textInput.value.trim();
		if (!text) { showError('Enter some text'); return; }
	}
	if (!CURRENT_VOICE_ID) { showError('Select a voice'); return; }

	const checkText = selFile ? (selFile.text || '') : (textInput.value || '');
	if (hasSignificantChinese(checkText) && CURRENT_VOICE_MODE === 'low') {
		showToast('Cảnh báo: Text appears to be Chinese. Translate to Vietnamese before using Vietnamese voice.');
	}

	hideError();
	hideFinalAudio();
	showProgressPopup();
	mergeSection.classList.add('hidden');
	resetBtn.style.display = 'inline-flex';
	generateBtn.disabled = true;
	STATE = 'loading';
	GENERATION_PAUSED = false;
	GENERATION_START_TIME = Date.now();

	const text = selFile ? selFile.text : textInput.value.trim();
	const c = selFile?.config || {};
	const mode = c.voice_mode || CURRENT_VOICE_MODE;
	const vid = c.voice_id || CURRENT_VOICE_ID;

	if (selFile) {
		selFile.status = 'processing';
		renderFileQueue();
	}

	setProgress(0, selFile ? 'Generating: ' + selFile.name : 'Starting...');

	try {
		const res = await fetch(`${API_BASE}/tts/generate`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				text, voice_mode: mode, voice_id: vid, output_format: 'mp3',
				normalize: c.normalize !== undefined ? c.normalize : document.getElementById('normalizeCheckbox').checked,
				clean: c.clean !== undefined ? c.clean : document.getElementById('cleanCheckbox').checked,
				normalize_audio: c.normalize_audio !== undefined ? c.normalize_audio : document.getElementById('normalizeAudioCheckbox').checked,
				speed: c.speed !== undefined ? c.speed : parseFloat(document.getElementById('speedSlider').value),
				volume: c.volume !== undefined ? c.volume : parseFloat(document.getElementById('volumeSlider').value),
				pitch: 0,
				split_segments: true,
				split_mode: c.split_mode || document.getElementById('splitModeSelect').value,
				cfg_strength: parseFloat(document.getElementById('cfgSlider').value),
				steps: parseInt(document.getElementById('stepsSlider').value),
				sway: parseFloat(document.getElementById('swaySlider').value),
				num_step: parseInt(document.getElementById('numStepSlider').value),
			}),
		});
		if (!res.ok) throw new Error('Generation failed');
		const data = await res.json();
		CURRENT_TASK_ID = data.task_id;
		if (selFile) selFile.task_id = data.task_id;
		pollStatus(CURRENT_TASK_ID, selFile);
	} catch (e) { onError(e.message); }
}

function pollStatus(taskId, fileRef) {
	if (POLL_INTERVAL) clearInterval(POLL_INTERVAL);
	let requestInFlight = false;
	let lastChunkSignature = '';
	POLL_INTERVAL = setInterval(async () => {
		if (requestInFlight) return;
		requestInFlight = true;
		try {
			const res = await fetch(`${API_BASE}/tts/status/${taskId}`);
			if (!res.ok) throw new Error('Status fetch failed');
			const data = await res.json();
			setProgress(data.progress, data.stage || '');
			const chunks = data.chunks || [];
			const signature = chunks.map(c => [c.index, c.status, c.audio_url, c.duration, c.warning, c.error, c.text, c.voice_id].join('|')).join('~');
			if (signature !== lastChunkSignature) {
				lastChunkSignature = signature;
				renderChunks(chunks);
			}

			if (data.status === 'chunks_done') {
				clearInterval(POLL_INTERVAL); POLL_INTERVAL = null;
				if (chunks.length > 1) {
					// Auto-merge multi-segment results
					try {
						const mergeRes = await fetch(`${API_BASE}/tts/merge`, {
							method: 'POST', headers: { 'Content-Type': 'application/json' },
							body: JSON.stringify({ task_id: taskId, output_format: 'mp3' }),
						});
						if (mergeRes.ok) {
							const mergeData = await mergeRes.json();
							data.audio_url = mergeData.audio_url;
							data.duration = mergeData.duration;
							data.status = 'done';
						}
					} catch (_) { }
				}
				if (fileRef) {
					fileRef.status = 'done';
					fileRef.chunks = chunks;
					fileRef.audio_url = data.audio_url || (chunks.find(c => c.audio_url)?.audio_url) || null;
					fileRef.duration = data.duration || null;
					fileRef.voice = (ALL_VOICES[CURRENT_VOICE_MODE] || []).find(v => v.id === CURRENT_VOICE_ID)?.label || CURRENT_VOICE_ID;
					renderFileQueue();
				}
				if (data.status === 'done') {
					renderChunks(data.chunks || []);
					if (chunks.length > 1) mergeSection.classList.remove('hidden');
					hideProgressPopup();
					showFinalAudio();
					CURRENT_AUDIO_URL = `${API_BASE}${data.audio_url}`;
					loadAudio(CURRENT_AUDIO_URL);
					generateBtn.disabled = false; resetBtn.style.display = 'inline-flex'; STATE = 'done';
				} else {
					onChunksDone(data);
				}
			} else if (data.status === 'done') {
				clearInterval(POLL_INTERVAL); POLL_INTERVAL = null;
				if (fileRef) {
					fileRef.status = 'done';
					fileRef.audio_url = data.audio_url || fileRef.audio_url;
					fileRef.duration = data.duration || fileRef.duration;
					renderFileQueue();
				}
				hideProgressPopup();
				showFinalAudio();
				CURRENT_AUDIO_URL = `${API_BASE}${data.audio_url}`;
				loadAudio(CURRENT_AUDIO_URL);
				generateBtn.disabled = false; resetBtn.style.display = 'inline-flex'; STATE = 'done';
			} else if (data.status === 'error') {
				clearInterval(POLL_INTERVAL); POLL_INTERVAL = null;
				if (fileRef) {
					fileRef.status = 'error';
					fileRef.error = data.error || 'Unknown error';
					renderFileQueue();
				}
				onError(data.error || 'Unknown error');
			}
		} catch (e) {
			clearInterval(POLL_INTERVAL); POLL_INTERVAL = null;
			onError(e.message);
		} finally {
			requestInFlight = false;
		}
	}, 500);
}

function onChunksDone(data) {
	STATE = 'chunks_done';
	generateBtn.disabled = false;
	hideProgressPopup();
	const chunks = data.chunks || [];
	if (chunks.length > 1) {
		mergeSection.classList.remove('hidden');
	} else if (chunks.length === 1 && chunks[0].status === 'done') {
		// Single chunk (split OFF) — show in Final Audio bar
		CURRENT_AUDIO_URL = `${API_BASE}${chunks[0].audio_url}`;
		loadAudio(CURRENT_AUDIO_URL);
		showFinalAudio();
	}
	resetBtn.style.display = 'inline-flex';
	updateAutosaveIndicator();
}

async function doMerge() {
	if (!CURRENT_TASK_ID) return;
	try {
		const res = await fetch(`${API_BASE}/tts/merge`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ task_id: CURRENT_TASK_ID, output_format: 'mp3' }),
		});
		if (!res.ok) throw new Error('Merge failed');
		const data = await res.json();
		showFinalAudio();
		CURRENT_AUDIO_URL = `${API_BASE}${data.audio_url}`;
		loadAudio(CURRENT_AUDIO_URL);
	} catch (e) { showError('Auto-merge: ' + e.message); }
}

function setProgress(pct, label) {
	floatFill.style.width = Math.min(pct, 100) + '%';

	const chunks = CHUNK_DATA || [];
	const done = chunks.filter(c => c.status === 'done').length;
	const total = chunks.length;

	if (total > 0) {
		floatCount.textContent = `${done} / ${total}`;
	} else if (label) {
		floatCount.textContent = `${Math.round(pct)}%`;
	} else {
		floatCount.textContent = `${Math.round(pct)}%`;
	}

	if (GENERATION_PAUSED) {
		floatStatus.textContent = 'Paused';
	} else if (pct < 100) {
		floatStatus.textContent = 'Generating audio...';
	} else {
		floatStatus.textContent = 'Complete';
	}

	// Elapsed time
	if (GENERATION_START_TIME && pct < 100) {
		const elapsed = (Date.now() - GENERATION_START_TIME) / 1000;
		const min = Math.floor(elapsed / 60);
		const sec = Math.floor(elapsed % 60);
		floatEta.textContent = `${min}:${sec.toString().padStart(2, '0')} elapsed`;
	} else if (pct >= 100) {
		const total = (Date.now() - GENERATION_START_TIME) / 1000;
		const min = Math.floor(total / 60);
		const sec = Math.floor(total % 60);
		floatEta.textContent = `Done in ${min}:${sec.toString().padStart(2, '0')}`;
	} else {
		floatEta.textContent = '0:00';
	}
}

function drawWaveform(audioUrl) {
	const canvas = document.getElementById('waveformCanvas');
	if (!canvas) return;
	canvas.style.display = 'block';
	const ctx = canvas.getContext('2d');
	const w = 600;
	const h = 160;
	canvas.width = w;
	canvas.height = h;
	ctx.clearRect(0, 0, w, h);
	ctx.fillStyle = '#2a2a2a';
	ctx.fillRect(0, 0, w, h);

	fetch(audioUrl).then(r => r.arrayBuffer()).then(buf => {
		const actx = new (window.AudioContext || window.webkitAudioContext)();
		return actx.decodeAudioData(buf);
	}).then(audioBuffer => {
		const data = audioBuffer.getChannelData(0);
		const step = Math.max(1, Math.floor(data.length / w));
		for (let i = 0; i < w; i++) {
			let peak = 0;
			const start = i * step;
			const end = Math.min(start + step, data.length);
			for (let j = start; j < end; j++) {
				const v = Math.abs(data[j]);
				if (v > peak) peak = v;
			}
			const barH = peak * h * 0.8;
			const progress = audioPlayer.duration > 0 ? audioPlayer.currentTime / audioPlayer.duration : 0;
			if (i / w < progress) {
				ctx.fillStyle = '#5b7a6a';
			} else {
				ctx.fillStyle = '#4a4a4a';
			}
			ctx.fillRect(i, (h - barH) / 2, 1, Math.max(1, barH));
		}
	}).catch(e => {
		ctx.fillStyle = '#666';
		ctx.font = '12px sans-serif';
		ctx.textAlign = 'center';
		ctx.fillText('Waveform unavailable', w / 2, h / 2);
	});
}

// Custom player controls
const cpPlayBtn = document.getElementById('cpPlayBtn');
const cpProgressBg = document.getElementById('cpProgressBg');
const cpProgressFill = document.getElementById('cpProgressFill');
const cpVolBtn = document.getElementById('cpVolBtn');
const cpVolSlider = document.getElementById('cpVolSlider');
const faCurrentTime = document.getElementById('faCurrentTime');
const faTotalTime = document.getElementById('faTotalTime');
const cpRewindBtn = document.getElementById('cpRewindBtn');
const cpForwardBtn = document.getElementById('cpForwardBtn');
const faSpeedSelect = document.getElementById('faSpeedSelect');

function formatTime(s) {
	if (!s || isNaN(s)) return '0:00';
	const m = Math.floor(s / 60);
	const sec = Math.floor(s % 60);
	return `${m}:${sec.toString().padStart(2, '0')}`;
}

function updatePlayIcon() {
	cpPlayBtn.innerHTML = audioPlayer.paused
		? '<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>'
		: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>';
}

cpPlayBtn.addEventListener('click', () => {
	if (audioPlayer.paused) audioPlayer.play().catch(() => { });
	else audioPlayer.pause();
});

cpProgressBg.addEventListener('click', e => {
	if (!audioPlayer.duration) return;
	const rect = cpProgressBg.getBoundingClientRect();
	const pct = (e.clientX - rect.left) / rect.width;
	const target = pct * audioPlayer.duration;
	audioPlayer.currentTime = target;
});

cpRewindBtn.addEventListener('click', () => {
	if (!audioPlayer.duration) return;
	const ct = audioPlayer.currentTime || 0;
	const target = Math.max(0, ct - 2);
	audioPlayer.currentTime = target;
});

cpForwardBtn.addEventListener('click', () => {
	if (!audioPlayer.duration) return;
	const ct = audioPlayer.currentTime || 0;
	const target = Math.min(audioPlayer.duration, ct + 2);
	audioPlayer.currentTime = target;
});

cpVolSlider.addEventListener('input', () => {
	audioPlayer.volume = parseFloat(cpVolSlider.value);
});

faSpeedSelect.addEventListener('change', () => {
	audioPlayer.playbackRate = parseFloat(faSpeedSelect.value);
});

audioPlayer.addEventListener('play', updatePlayIcon);
audioPlayer.addEventListener('pause', updatePlayIcon);
audioPlayer.addEventListener('timeupdate', () => {
	if (!audioPlayer.duration) return;
	const ct = audioPlayer.currentTime;
	if (typeof ct !== 'number' || !isFinite(ct)) {
		return;
	}
	faCurrentTime.textContent = formatTime(ct);
	faTotalTime.textContent = formatTime(audioPlayer.duration);
	cpProgressFill.style.width = ((ct / audioPlayer.duration) * 100) + '%';
});
audioPlayer.addEventListener('loadedmetadata', () => {
	faTotalTime.textContent = formatTime(audioPlayer.duration);
});


// Waveform seek interaction
const waveformWrap = document.getElementById('waveformWrap');
const waveformSeek = document.getElementById('waveformSeek');
const waveformTime = document.getElementById('waveformTime');

if (waveformWrap) {
	waveformWrap.addEventListener('click', e => {
		if (!audioPlayer.duration) return;
		const rect = waveformWrap.getBoundingClientRect();
		const pct = (e.clientX - rect.left) / rect.width;
		const target = pct * audioPlayer.duration;
		audioPlayer.currentTime = target;
	});

	waveformWrap.addEventListener('mousemove', e => {
		if (!audioPlayer.duration) return;
		const rect = waveformWrap.getBoundingClientRect();
		let pct = (e.clientX - rect.left) / rect.width;
		if (pct < 0) pct = 0;
		if (pct > 1) pct = 1;
		const ht = pct * audioPlayer.duration;
		waveformTime.textContent = `${Math.floor(ht / 60)}:${Math.floor(ht % 60).toString().padStart(2, '0')}`;
		waveformSeek.style.width = (pct * 100) + '%';
		waveformTime.style.opacity = '1';
	});

	waveformWrap.addEventListener('mouseleave', () => {
		if (!audioPlayer.duration) return;
		const progress = audioPlayer.currentTime / audioPlayer.duration;
		waveformSeek.style.width = (progress * 100) + '%';
		waveformTime.style.opacity = '0';
	});

	audioPlayer.addEventListener('timeupdate', () => {
		if (!audioPlayer.duration) return;
		const progress = audioPlayer.currentTime / audioPlayer.duration;
		waveformSeek.style.width = (progress * 100) + '%';
	});
}

function loadAudio(url) {
	audioPlayer.src = url;
	audioPlayer.load();
	drawWaveform(url);
}

function onError(msg) {
	STATE = 'error';
	generateBtn.disabled = false;
	hideProgressPopup();
	showError(msg);
}

mergeBtn.addEventListener('click', async () => {
	if (!CURRENT_TASK_ID) return;
	mergeBtn.disabled = true; mergeBtn.textContent = 'Merging...';
	showProgressPopup();
	setProgress(90, 'Merging audio...');
	await doMerge();
	mergeBtn.disabled = false; mergeBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:18px;height:18px;"><path d="M8 6L21 6"/><path d="M8 12L21 12"/><path d="M8 18L21 18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg> Merge &amp; Download All';
	hideProgressPopup();
});

document.getElementById('downloadAllBtn').addEventListener('click', () => {
	downloadAllChunks();
});

downloadBtn.addEventListener('click', () => {
	if (!CURRENT_AUDIO_URL) return;
	const fmt = formatSelect.value;
	const url = CURRENT_AUDIO_URL + '&format=' + fmt;
	const ext = fmt === 'mp3_320' ? 'mp3' : fmt;
	const ts = new Date().toISOString().slice(0, 19).replace(/[:-]/g, '');
	const a = document.createElement('a');
	a.href = url; a.download = `capcap_${ts}.${ext}`; a.click();
});

document.getElementById('remergeBtn').addEventListener('click', async () => {
	if (!CURRENT_TASK_ID) return;
	await doMerge();
	showToast('Re-merged audio');
});

// Close final audio bar
document.getElementById('closeFinalAudio').addEventListener('click', hideFinalAudio);
finalAudioOverlay.addEventListener('click', hideFinalAudio);

// Reopen final audio bar
reopenFinalBtn.addEventListener('click', showFinalAudio);

resetBtn.addEventListener('click', async () => {
	if (!CURRENT_TASK_ID) return;
	if (!confirm('Reset this session? All generated audio will be deleted.')) return;
	try {
		await fetch(`${API_BASE}/tts/reset/${CURRENT_TASK_ID}`, { method: 'POST' });
	} catch (_) { }
	CURRENT_TASK_ID = null;
	CURRENT_AUDIO_URL = null;
	CHUNK_DATA = [];
	if (POLL_INTERVAL) { clearInterval(POLL_INTERVAL); POLL_INTERVAL = null; }
	STATE = 'idle';
	generateBtn.disabled = false;
	hideProgressPopup();
	hideFinalAudio();
	reopenFinalBtn.classList.remove('visible');
	mergeSection.classList.add('hidden');
	chunkList.innerHTML = '<div class="no-chunks">Enter text and click Generate to see segments</div>';
	resetBtn.style.display = 'none';
	hideError();
});

floatCancelBtn.addEventListener('click', async () => {
	if (!CURRENT_TASK_ID) return;
	if (!confirm('Cancel generation?')) return;
	if (POLL_INTERVAL) { clearInterval(POLL_INTERVAL); POLL_INTERVAL = null; }
	try {
		await fetch(`${API_BASE}/tts/reset/${CURRENT_TASK_ID}`, { method: 'POST' });
	} catch (_) { }
	CURRENT_TASK_ID = null;
	STATE = 'idle';
	generateBtn.disabled = false;
	hideProgressPopup();
	resetBtn.style.display = 'none';
	showToast('Generation cancelled');
});

/* Dictionary button */
document.getElementById('dictBtn').addEventListener('click', () => {
	loadDict();
	document.getElementById('dictModal').classList.remove('hidden');
	document.querySelector('#dictModal .dict-tab[data-tab=acronyms]').click();
});
document.getElementById('dictCloseBtn').addEventListener('click', () => {
	document.getElementById('dictModal').classList.add('hidden');
});
document.getElementById('dictModal').addEventListener('click', e => {
	if (e.target === e.currentTarget) document.getElementById('dictModal').classList.add('hidden');
});

/* Pause modal */
const PAUSE_LABELS = { '.': 'Period (.)', ',': 'Comma (,)', ';': 'Semicolon (;)', ':': 'Colon (:)', '?': 'Question (?)', '!': 'Exclamation (!)', 'linebreak': 'Line break' };

async function loadPauseConfig() {
	try {
		const res = await fetch(`${API_BASE}/tts/pause_config`);
		const cfg = await res.json();
		const enabled = cfg.enabled !== false;
		const pauses = cfg.pauses || {};
		const toggle = document.getElementById('pauseToggle');
		toggle.checked = enabled;
		updatePauseToggleUI(enabled);
		const container = document.getElementById('pauseSliders');
		container.innerHTML = Object.keys(pauses).map(k => `
      <div class="pause-slider-row">
        <span class="pause-slider-label">${PAUSE_LABELS[k] || k}</span>
        <input type="range" min="0" max="2" step="0.05" value="${pauses[k]}" data-key="${k}">
        <span class="pause-slider-val">${pauses[k].toFixed(2)}s</span>
      </div>`).join('');
		container.querySelectorAll('input[type=range]').forEach(slider => {
			slider.addEventListener('input', onPauseSliderChange);
		});
	} catch (e) { console.error('Pause load:', e); }
}

function onPauseSliderChange(e) {
	const val = parseFloat(e.target.value);
	e.target.nextElementSibling.textContent = val.toFixed(2) + 's';
}

function onPauseToggle() {
	const enabled = document.getElementById('pauseToggle').checked;
	updatePauseToggleUI(enabled);
}

function updatePauseToggleUI(enabled) {
	const label = document.getElementById('pauseToggleLabel');
	label.textContent = enabled ? 'ON' : 'OFF';
	document.querySelectorAll('#pauseSliders input').forEach(s => s.disabled = !enabled);
}

async function savePauseConfig() {
	const enabled = document.getElementById('pauseToggle').checked;
	const sliders = document.querySelectorAll('#pauseSliders input[type=range]');
	const pauses = {};
	sliders.forEach(s => { pauses[s.dataset.key] = parseFloat(s.value); });
	try {
		const r = await fetch(`${API_BASE}/tts/pause_config`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ config: { enabled, pauses } }),
		});
		PAUSE_CFG = await r.json();
		updateCharCount();
	} catch (e) { console.error('Pause save:', e); }
}

document.getElementById('pauseBtn').addEventListener('click', () => {
	loadPauseConfig();
	document.getElementById('pauseModal').classList.remove('hidden');
});
document.getElementById('pauseCloseBtn').addEventListener('click', () => {
	document.getElementById('pauseModal').classList.add('hidden');
});
document.getElementById('pauseSaveBtn').addEventListener('click', async () => {
	await savePauseConfig();
	document.getElementById('pauseModal').classList.add('hidden');
});
document.getElementById('pauseModal').addEventListener('click', e => {
	if (e.target === e.currentTarget) document.getElementById('pauseModal').classList.add('hidden');
});

/* History */
document.getElementById('historyBtn').addEventListener('click', async () => {
	const modal = document.getElementById('historyModal');
	modal.classList.remove('hidden');
	await loadHistory();
});
document.getElementById('historyCloseBtn').addEventListener('click', () => {
	document.getElementById('historyModal').classList.add('hidden');
});
document.getElementById('historyClearBtn').addEventListener('click', async () => {
	if (!confirm('Clear all history and audio files?')) return;
	await fetch(`${API_BASE}/tts/history`, { method: 'DELETE' });
	await loadHistory();
});
document.getElementById('historyModal').addEventListener('click', e => {
	if (e.target === e.currentTarget) document.getElementById('historyModal').classList.add('hidden');
});

async function loadHistory() {
	try {
		const res = await fetch(`${API_BASE}/tts/history`);
		const entries = await res.json();
		const container = document.getElementById('historyList');
		if (!entries || entries.length === 0) {
			container.innerHTML = '<div class="hist-empty">No history yet</div>';
			return;
		}
		container.innerHTML = entries.map((e, i) => {
			const date = new Date((e.timestamp || 0) * 1000);
			const timeStr = date.toLocaleString();
			const txt = (e.text || '').slice(0, 80);
			return `<div class="hist-item">
        <div class="hist-info">
          <div class="hist-text">${escapeHtml(txt)}</div>
          <div class="hist-meta">${timeStr} &middot; ${e.duration || '?'}s &middot; ${e.voice_id || ''}</div>
        </div>
        <div class="hist-actions">
          <button onclick="playHistory('${e.id}')" title="Play">
            <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          </button>
          <button onclick="downloadHistory('${e.id}')" title="Download">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
          </button>
          <button onclick="deleteHistory('${e.id}')" title="Delete">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
      </div>`;
		}).join('');
	} catch (e_) { console.error('History:', e_); }
}

function playHistory(id) {
	const url = `${API_BASE}/tts/download_file?path=${id}/final.mp3`;
	prepareMiniPlayer();
	removeMiniPlayer();
	const wrap = document.createElement('div');
	wrap.id = 'chunkPlayer';
	wrap.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-md);padding:10px 18px;box-shadow:var(--shadow-lg);z-index:300;display:flex;align-items:center;gap:12px;';
	const aud = document.createElement('audio');
	aud.src = url;
	aud.controls = true;
	aud.style.cssText = 'height:34px;';
	const close = document.createElement('button');
	close.innerHTML = '&times;';
	close.style.cssText = 'border:none;background:none;font-size:22px;cursor:pointer;color:var(--text-muted);padding:0 4px;line-height:1;';
	close.onclick = () => closeMiniPlayer(wrap);
	wrap.appendChild(aud);
	wrap.appendChild(close);
	document.body.appendChild(wrap);
	aud.play().catch(() => { });
}

function downloadHistory(id) {
	const a = document.createElement('a');
	a.href = `${API_BASE}/tts/download_file?path=${id}/final.mp3`;
	a.download = `tts_${id}.mp3`;
	a.click();
}

async function deleteHistory(id) {
	await fetch(`${API_BASE}/tts/history/${id}`, { method: 'DELETE' });
	await loadHistory();
}

function toggleSingleEdit() {
	const display = document.getElementById('singleTextDisplay');
	const ta = document.getElementById('singleTextEdit');
	const btn = document.getElementById('singleEditBtn');
	if (!display || !ta) return;
	if (ta.style.display === 'none') {
		display.style.display = 'none';
		ta.style.display = 'block';
		ta.focus();
		ta.style.borderColor = 'var(--accent-primary)';
		btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;"><polyline points="20 6 9 17 4 12"/></svg> Save';
	} else {
		display.style.display = 'block';
		ta.style.display = 'none';
		display.textContent = ta.value;
		const c = CHUNK_DATA.find(x => x.index === 0);
		if (c) c.text = ta.value.trim();
		btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> Edit';
		showToast('Text updated');
	}
}

async function retrySingle() {
	const btn = document.getElementById('singleRetryBtn');
	if (!btn) return;
	const ta = document.getElementById('singleTextEdit');
	const display = document.getElementById('singleTextDisplay');
	const text = ta && ta.style.display !== 'none' ? ta.value.trim() : (display ? display.textContent.trim() : '');
	if (!text) return;

	btn.disabled = true;
	btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke-dasharray="30 70"/></svg> Retrying...';

	await _regenChunk(0, text);

	btn.disabled = false;
	btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Retry';
}

/* Chunk rendering */
function renderChunks(chunks) {
	if (!chunks || chunks.length === 0) {
		chunkList.innerHTML = '<div class="no-chunks">No segments yet. Generate audio to begin.</div>';
		chunkCount.textContent = '';
		chunkPagination.style.display = 'none';
		mergeSection.classList.add('hidden');
		segmentFilters.style.display = 'none';
		batchActions.style.display = 'none';
		return;
	}

	CHUNK_DATA = chunks;
	const doneCount = chunks.filter(c => c.status === 'done').length;
	const warningCount = chunks.filter(c => c.warning).length;
	const errorCount = chunks.filter(c => c.status === 'error').length;

	chunkCount.textContent = `(${chunks.length} segments · ${doneCount} done${warningCount > 0 ? ` · ${warningCount} ⚠` : ''}${errorCount > 0 ? ` · ${errorCount} ✗` : ''})`;

	// Show filters and batch actions when multiple chunks
	if (chunks.length > 1) {
		segmentFilters.style.display = 'flex';
		batchActions.style.display = 'flex';
	} else {
		segmentFilters.style.display = 'none';
		batchActions.style.display = 'none';
	}

	chunkPagination.style.display = 'none';

	// Filter chunks
	let filteredChunks = chunks;
	if (CURRENT_FILTER !== 'all') {
		if (CURRENT_FILTER === 'done') filteredChunks = chunks.filter(c => c.status === 'done');
		else if (CURRENT_FILTER === 'warning') filteredChunks = chunks.filter(c => c.warning);
		else if (CURRENT_FILTER === 'failed') filteredChunks = chunks.filter(c => c.status === 'error');
		else if (CURRENT_FILTER === 'processing') filteredChunks = chunks.filter(c => c.status === 'processing');
	}

	// Single chunk (non-split mode)
	if (chunks.length === 1) {
		const c = chunks[0];
		const isDone = c.status === 'done';
		const isProcessing = c.status === 'processing';
		chunkList.innerHTML = `
      <div class="card" style="margin:0;padding:16px;background:var(--bg-primary);border-radius:var(--radius-md);min-height:200px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
          <span style="font-size:12px;color:var(--text-muted);font-weight:600;text-transform:uppercase;">Generated Audio</span>
          <div style="display:flex;gap:6px;">
            ${isDone ? `<button class="btn-sm" id="singleEditBtn" onclick="toggleSingleEdit()">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              Edit
            </button>` : ''}
            ${isDone ? `<button class="btn-sm" id="singleRetryBtn" onclick="retrySingle()">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
              Retry
            </button>` : ''}
          </div>
        </div>
        <div id="singleTextDisplay" style="width:100%;min-height:120px;font-size:13px;background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-sm);padding:10px;color:var(--text-primary);white-space:pre-wrap;word-break:break-word;">${escapeHtml(c.text)}</div>
        <textarea id="singleTextEdit" style="display:none;width:100%;min-height:120px;font-size:13px;background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-sm);padding:10px;color:var(--text-primary);font-family:inherit;resize:vertical;outline:none;">${escapeHtml(c.text)}</textarea>
        ${isDone ? `<div style="margin-top:12px;text-align:center;color:var(--text-muted);font-size:13px;">✓ Audio ready — see Final Audio bar below</div>` : `
        <div style="margin-top:12px;text-align:center;color:var(--text-muted);font-size:13px;padding:20px;">
          ${isProcessing ? '<span style="display:inline-block;width:12px;height:12px;border:2px solid var(--card-border);border-top-color:var(--accent-primary);border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle;margin-right:6px;"></span>Generating...' : 'Queued...'}
        </div>`}
      </div>`;
		mergeSection.classList.add('hidden');
		return;
	}

	// Multiple chunks (split mode) — show compact list with pagination
	const totalPages = Math.ceil(filteredChunks.length / CHUNK_PER_PAGE);
	if (CHUNK_PAGE >= totalPages) CHUNK_PAGE = totalPages - 1;
	if (CHUNK_PAGE < 0) CHUNK_PAGE = 0;

	const start = CHUNK_PAGE * CHUNK_PER_PAGE;
	const pageChunks = filteredChunks.slice(start, start + CHUNK_PER_PAGE);

	if (totalPages > 1) {
		chunkPagination.style.display = 'flex';
		pageInfo.textContent = `${CHUNK_PAGE + 1}/${totalPages}`;
		pagePrev.disabled = CHUNK_PAGE === 0;
		pageNext.disabled = CHUNK_PAGE >= totalPages - 1;
	} else {
		chunkPagination.style.display = 'none';
	}

	let html = '';
	for (const c of pageChunks) {
		const hasWarning = c.warning || false;
		const statusClass = c.status === 'processing' ? 'processing' : c.status === 'done' ? (hasWarning ? 'warning' : 'done') : c.status === 'error' ? 'error' : '';
		const icon = c.status === 'pending' ? '<svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px;"><circle cx="12" cy="12" r="4"/></svg>' : c.status === 'processing' ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="width:14px;height:14px;animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke-dasharray="30 70"/></svg>' : c.status === 'done' ? (hasWarning ? '<svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px;"><path d="M12 2L1 21h22L12 2zm0 4l7.53 13H4.47L12 6z"/><circle cx="12" cy="15" r="1.5"/><line x1="12" y1="9" x2="12" y2="12" stroke="currentColor" stroke-width="2"/></svg>' : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" style="width:14px;height:14px;"><polyline points="20 6 9 17 4 12"/></svg>') : c.status === 'error' ? '<svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px;"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>' : '<svg viewBox="0 0 24 24" fill="currentColor" style="width:14px;height:14px;"><circle cx="12" cy="12" r="4"/></svg>';
		const iconColor = c.status === 'pending' ? 'var(--text-muted)' : c.status === 'processing' ? '#4a9e7a' : c.status === 'done' ? (hasWarning ? '#d4a832' : 'var(--success)') : '#c44';
		const preview = c.text.length > 60 ? c.text.slice(0, 60) + '...' : c.text;
		const duration = c.duration ? ` ${formatDuration(c.duration)}` : '';
		const glowClass = c.status === 'processing' ? 'segment-glow' : '';

		html += `
      <div class="chunk-item ${statusClass} ${glowClass}" data-index="${c.index}" data-status="${c.status}" data-warning="${hasWarning}">
        <div class="chunk-header" onclick="toggleChunk(${c.index})">
          <span class="chunk-index">${c.index + 1}</span>
          <span class="chunk-status-icon" style="color:${iconColor}">${icon}</span>
          <span class="chunk-text-preview">${escapeHtml(preview)}</span>
          ${duration ? `<span style="font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;flex-shrink:0;">${duration}</span>` : ''}
          <span class="chunk-actions">
            ${c.status === 'done' ? `<button onclick="event.stopPropagation();playChunk(${c.index})" title="Play"><svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg></button>` : ''}
            ${c.status === 'done' ? `<button onclick="event.stopPropagation();downloadChunk(${c.index})" title="Download"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>` : ''}
            ${c.status === 'done' || c.status === 'error' ? `<button onclick="event.stopPropagation();retryChunk(${c.index})" title="Retry"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg></button>` : ''}
            <button onclick="event.stopPropagation();toggleEdit(${c.index})" title="Edit" id="editBtn_${c.index}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>
          </span>
        </div>
        <div class="chunk-body" id="chunkBody_${c.index}">
          ${chunks.length > 1 ? `<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap;">
            <span style="font-size:12px;color:var(--text-muted);flex-shrink:0;font-weight:600;">Voice:</span>
            <select id="chunkVoice_${c.index}" onchange="updateChunkVoice(${c.index},this.value)" style="flex:1;min-width:120px;padding:5px 8px;border:1px solid var(--card-border);border-radius:var(--radius-sm);font-size:12px;background:var(--card-bg);color:var(--text-secondary);font-family:inherit;">
              ${_voiceOptGroups(CURRENT_VOICE_MODE, c.voice_id || CURRENT_VOICE_ID, '', '')}
            </select>
            <select id="chunkVoiceGender_${c.index}" onchange="_refreshChunkVoices(${c.index})" style="padding:5px 6px;border:1px solid var(--card-border);border-radius:var(--radius-sm);font-size:11px;background:var(--card-bg);color:var(--text-secondary);font-family:inherit;">
              <option value="">Gender</option>
              <option value="male">♂ Male</option>
              <option value="female">♀ Female</option>
            </select>
            <select id="chunkVoiceType_${c.index}" onchange="_refreshChunkVoices(${c.index})" style="padding:5px 6px;border:1px solid var(--card-border);border-radius:var(--radius-sm);font-size:11px;background:var(--card-bg);color:var(--text-secondary);font-family:inherit;">
              <option value="">Type</option>
              <option value="clone">Clone</option>
              <option value="default">Default</option>
            </select>
          </div>` : ''}
          <textarea id="chunkText_${c.index}" readonly>${escapeHtml(c.text)}</textarea>
          ${hasWarning || (c.issues && c.issues.length > 0) ? `<div style="margin-top:8px;padding:8px 12px;background:var(--warning-light);border-radius:var(--radius-sm);font-size:12px;color:var(--warning);">${(c.issues || []).map(i => `<div style="margin:2px 0">⚠ <strong>${i.message}</strong>${i.details && i.details.duration_sec ? ` (${i.details.duration_sec}s)` : ''}</div>`).join('') || '⚠ Audio quality warning detected'}</div>` : ''}
        </div>
      </div>`;
	}
	chunkList.innerHTML = html;
	// Restore expanded chunks after re-render
	for (const idx of _expandedChunks) {
		const body = document.getElementById(`chunkBody_${idx}`);
		if (body) body.classList.add('open');
	}
}

function formatDuration(seconds) {
	const min = Math.floor(seconds / 60);
	const sec = Math.floor(seconds % 60);
	return min > 0 ? `${min}:${sec.toString().padStart(2, '0')}` : `0:${sec.toString().padStart(2, '0')}`;
}

pagePrev.addEventListener('click', () => { if (CHUNK_PAGE > 0) { CHUNK_PAGE--; renderChunks(CHUNK_DATA); } });
pageNext.addEventListener('click', () => {
	const totalPages = Math.ceil(CHUNK_DATA.length / CHUNK_PER_PAGE);
	if (CHUNK_PAGE < totalPages - 1) { CHUNK_PAGE++; renderChunks(CHUNK_DATA); }
});

// Segment filters
document.querySelectorAll('.segment-filter-btn').forEach(btn => {
	btn.addEventListener('click', () => {
		document.querySelectorAll('.segment-filter-btn').forEach(b => b.classList.remove('active'));
		btn.classList.add('active');
		CURRENT_FILTER = btn.dataset.filter;
		CHUNK_PAGE = 0;
		renderChunks(CHUNK_DATA);
	});
});

// Batch actions
document.getElementById('retryFailedBtn').addEventListener('click', async () => {
	const failed = CHUNK_DATA.filter(c => c.status === 'error');
	if (failed.length === 0) { showToast('No failed segments'); return; }
	for (const c of failed) {
		await _regenChunk(c.index, c.text);
	}
	showToast(`Retrying ${failed.length} failed segment${failed.length > 1 ? 's' : ''}`);
});

document.getElementById('retryWarningBtn').addEventListener('click', async () => {
	const warnings = CHUNK_DATA.filter(c => c.warning);
	if (warnings.length === 0) { showToast('No warning segments'); return; }
	for (const c of warnings) {
		await _regenChunk(c.index, c.text);
	}
	showToast(`Retrying ${warnings.length} warning segment${warnings.length > 1 ? 's' : ''}`);
});

document.getElementById('collapseAllBtn').addEventListener('click', () => {
	document.querySelectorAll('.chunk-body').forEach(body => body.classList.remove('open'));
});

document.getElementById('expandFailedBtn').addEventListener('click', () => {
	document.querySelectorAll('.chunk-body').forEach(body => body.classList.remove('open'));
	CHUNK_DATA.filter(c => c.status === 'error' || c.warning).forEach(c => {
		const body = document.getElementById(`chunkBody_${c.index}`);
		if (body) body.classList.add('open');
	});
});

function toggleAdv(el) {
	const p = document.getElementById('advPanel');
	p.classList.toggle('hidden');
	el.classList.toggle('open');
}

function toggleChunk(index) {
	const body = document.getElementById(`chunkBody_${index}`);
	if (!body) return;
	body.classList.toggle('open');
	if (body.classList.contains('open')) {
		_expandedChunks.add(index);
	} else {
		_expandedChunks.delete(index);
	}
}

function toggleEdit(index) {
	const ta = document.getElementById(`chunkText_${index}`);
	const body = document.getElementById(`chunkBody_${index}`);
	const btn = document.getElementById(`editBtn_${index}`);
	if (!ta || !body) return;

	// Expand body if collapsed
	if (!body.classList.contains('open')) {
		body.classList.add('open');
	}

	// Toggle readonly
	if (ta.readOnly) {
		ta.readOnly = false;
		ta.focus();
		ta.style.borderColor = 'var(--accent-primary)';
		ta.style.boxShadow = '0 0 0 3px rgba(91,122,106,0.1)';
		btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
		btn.title = 'Save';
	} else {
		ta.readOnly = true;
		ta.style.borderColor = '';
		ta.style.boxShadow = '';
		btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
		btn.title = 'Edit';
		// Update chunk data with edited text
		const c = CHUNK_DATA.find(x => x.index === index);
		if (c) c.text = ta.value.trim();
		showToast('Segment ' + (index + 1) + ' updated');
	}
}

function escapeHtml(s) {
	const d = document.createElement('div');
	d.textContent = s;
	return d.innerHTML;
}

function downloadChunk(index) {
	const d = CHUNK_DATA.find(x => x.index === index);
	if (!d || !d.audio_url) return;
	const a = document.createElement('a');
	a.href = `${API_BASE}${d.audio_url}&format=wav`;
	a.download = `segment_${index + 1}.wav`;
	a.click();
}

function downloadAllChunks() {
	const done = CHUNK_DATA.filter(c => c.status === 'done' && c.audio_url);
	if (done.length === 0) { showToast('No segments to download'); return; }
	if (typeof JSZip !== 'undefined') {
		const zip = new JSZip();
		showToast('Preparing zip...');
		Promise.all(done.map(async c => {
			try {
				const res = await fetch(`${API_BASE}${c.audio_url}&format=wav`);
				if (res.ok) zip.file(`segment_${c.index + 1}.wav`, await res.blob());
			} catch (_) { }
		})).then(() => zip.generateAsync({ type: 'blob' })).then(blob => {
			const a = document.createElement('a');
			a.href = URL.createObjectURL(blob);
			a.download = `segments_${new Date().toISOString().slice(0, 19).replace(/[:-]/g, '')}.zip`;
			a.click(); URL.revokeObjectURL(a.href);
			showToast(`Downloaded ${done.length} segments`);
		});
	} else {
		for (const c of done) {
			setTimeout(() => {
				const a = document.createElement('a');
				a.href = `${API_BASE}${c.audio_url}&format=wav`;
				a.download = `segment_${c.index + 1}.wav`;
				a.click();
			}, c.index * 500);
		}
		showToast(`Downloading ${done.length} segments`);
	}
}

function _refreshChunkVoices(index) {
	const sel = document.getElementById('chunkVoice_' + index);
	if (!sel) return;
	const c = CHUNK_DATA.find(x => x.index === index);
	if (!c) return;
	const gf = document.getElementById('chunkVoiceGender_' + index)?.value || '';
	const tf = document.getElementById('chunkVoiceType_' + index)?.value || '';
	const cur = c.voice_id || CURRENT_VOICE_ID;
	sel.innerHTML = _voiceOptGroups(CURRENT_VOICE_MODE, cur, gf, tf);
}

function _voiceOptGroups(mode, selectedId, genderFilter, typeFilter) {
	let list = ALL_VOICES[mode] || [];
	if (genderFilter) list = list.filter(v => v.gender === genderFilter);
	if (typeFilter === 'clone') list = list.filter(v => v.is_clone);
	else if (typeFilter === 'default') list = list.filter(v => !v.is_clone);
	const clones = list.filter(v => v.is_clone);
	const defaults = list.filter(v => !v.is_clone);
	const opt = (v) => {
		const g = v.gender === 'male' ? '\u2642 ' : v.gender === 'female' ? '\u2640 ' : '';
		const sel = v.id === selectedId ? ' selected' : '';
		return `<option value="${v.id}"${sel}>${g}${escapeHtml(v.label)}</option>`;
	};
	let html = '';
	if (clones.length) html += `<optgroup label="\u2605 Clone Voices">${clones.map(opt).join('')}</optgroup>`;
	if (defaults.length) html += `<optgroup label="Default Voices">${defaults.map(opt).join('')}</optgroup>`;
	return html;
}

function updateChunkVoice(index, voiceId) {
	const c = CHUNK_DATA.find(x => x.index === index);
	if (c) {
		c.voice_id = voiceId;
	}
}

function playChunk(index) {
	const c = CHUNK_DATA.find(x => x.index === index);
	if (!c || !c.audio_url) return;
	prepareMiniPlayer();
	removeMiniPlayer();
	const wrap = document.createElement('div');
	wrap.id = 'chunkPlayer';
	wrap.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--card-bg);border:1px solid var(--card-border);border-radius:var(--radius-md);padding:10px 18px;box-shadow:var(--shadow-lg);z-index:300;display:flex;align-items:center;gap:12px;';
	const label = document.createElement('span');
	label.style.cssText = 'font-size:13px;color:var(--text-primary);white-space:nowrap;font-weight:600;';
	label.textContent = 'Segment ' + (index + 1) + ':';
	const aud = document.createElement('audio');
	aud.src = `${API_BASE}${c.audio_url}`;
	aud.controls = true;
	aud.style.cssText = 'height:34px;';
	const close = document.createElement('button');
	close.innerHTML = '&times;';
	close.style.cssText = 'border:none;background:none;font-size:22px;cursor:pointer;color:var(--text-muted);padding:0 4px;line-height:1;';
	close.onclick = () => closeMiniPlayer(wrap);
	wrap.appendChild(label);
	wrap.appendChild(aud);
	wrap.appendChild(close);
	document.body.appendChild(wrap);
	aud.play().catch(() => { });
}

async function retryChunk(index) {
	const btn = document.querySelector(`.chunk-item[data-index="${index}"] .chunk-actions button[title="Retry"]`);
	if (btn) {
		btn.disabled = true;
		btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;animation:spin 1s linear infinite;"><circle cx="12" cy="12" r="10" stroke-dasharray="30 70"/></svg>';
	}
	const ta = document.getElementById(`chunkText_${index}`);
	const text = ta ? ta.value.trim() : (CHUNK_DATA.find(x => x.index === index)?.text || '');
	if (!text) return;
	const c = CHUNK_DATA.find(x => x.index === index);
	if (c && ta) c.text = text;
	await _regenChunk(index, text);
	if (btn) {
		btn.disabled = false;
		btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
	}
}

async function _regenChunk(index, text) {
	if (!CURRENT_TASK_ID) return;
	const c = CHUNK_DATA.find(x => x.index === index);
	const vid = c?.voice_id || CURRENT_VOICE_ID;
	try {
		c.status = 'processing';
		renderChunks(CHUNK_DATA);
		const res = await fetch(`${API_BASE}/tts/regenerate_chunk`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ task_id: CURRENT_TASK_ID, chunk_index: index, text, voice_id: vid }),
		});
		if (!res.ok) throw new Error('Regen failed');
		const data = await res.json();
		c.status = data.status === 'done' ? 'done' : 'error';
		c.audio_url = data.audio_url;
		c.error = data.status === 'error' ? (data.issues?.[0]?.message || 'Regen error') : null;
		c.text = text;
		renderChunks(CHUNK_DATA);
		showToast(data.status === 'done' ? `Segment ${index + 1} regenerated` : `Segment ${index + 1} failed`);
	} catch (e) {
		c.status = 'error';
		c.error = e.message;
		renderChunks(CHUNK_DATA);
		showError('Regen chunk: ' + e.message);
	}
}

function showToast(msg) {
	const old = document.querySelector('.toast');
	if (old) old.remove();
	const t = document.createElement('div');
	t.className = 'toast'; t.textContent = msg;
	document.body.appendChild(t);
	setTimeout(() => t.remove(), 2000);
}

/* Dictionary */
function switchDictTab(tab) {
	document.querySelectorAll('.dict-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
	document.getElementById('dictAcronyms').classList.toggle('hidden', tab !== 'acronyms');
	document.getElementById('dictWords').classList.toggle('hidden', tab !== 'words');
}

async function loadDict() {
	try {
		const [ar, wr] = await Promise.all([
			fetch(`${API_BASE}/tts/dict/acronyms`).then(r => r.json()),
			fetch(`${API_BASE}/tts/dict/words`).then(r => r.json()),
		]);
		renderDictTable('acroBody', ar.entries, 'acronyms');
		renderDictTable('wordBody', wr.entries, 'words');
	} catch (e) { console.error('Dict load:', e); }
}

function renderDictTable(bodyId, entries, type) {
	const body = document.getElementById(bodyId);
	if (!entries || entries.length === 0) {
		body.innerHTML = '<tr><td colspan="3" style="color:var(--text-muted);text-align:center;padding:16px;font-size:12px;">No entries yet</td></tr>';
		return;
	}
	body.innerHTML = entries.map((e, i) => `
    <tr>
      <td><input type="text" value="${escapeHtml(e.key)}" id="${type}_key_${i}" placeholder="e.g. vtv"></td>
      <td><input type="text" value="${escapeHtml(e.value)}" id="${type}_val_${i}" placeholder="pronunciation"></td>
      <td style="white-space:nowrap;">
        <button class="btn-dict-del" onclick="saveDictRow('${type}', ${i})" title="Save">&#10003;</button>
        <button class="btn-dict-del" onclick="deleteDictRow('${type}', '${escapeHtml(e.key)}')" title="Delete">&#10005;</button>
      </td>
    </tr>`).join('');
}

async function saveDictRow(type, idx) {
	const keyEl = document.getElementById(`${type}_key_${idx}`);
	const valEl = document.getElementById(`${type}_val_${idx}`);
	const key = keyEl.value.trim();
	const value = valEl.value.trim();
	if (!key || !value) { showToast('Both fields required'); return; }

	const endpoint = type === 'acronyms' ? '/tts/dict/acronyms' : '/tts/dict/words';
	try {
		const res = await fetch(`${API_BASE}${endpoint}`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ key, value }),
		});
		if (!res.ok) throw new Error('Save failed');
		const data = await res.json();
		renderDictTable(type === 'acronyms' ? 'acroBody' : 'wordBody', data.entries, type);
		showToast('Saved');
	} catch (e) { showToast('Error: ' + e.message); }
}

async function deleteDictRow(type, key) {
	const endpoint = type === 'acronyms' ? '/tts/dict/acronyms' : '/tts/dict/words';
	try {
		const res = await fetch(`${API_BASE}${endpoint}?key=${encodeURIComponent(key)}`, { method: 'DELETE' });
		if (!res.ok) throw new Error('Delete failed');
		const data = await res.json();
		renderDictTable(type === 'acronyms' ? 'acroBody' : 'wordBody', data.entries, type);
		showToast('Deleted');
	} catch (e) { showToast('Error: ' + e.message); }
}

function addDictRow(type) {
	const bodyId = type === 'acronyms' ? 'acroBody' : 'wordBody';
	const body = document.getElementById(bodyId);
	const idx = body.children.length;
	body.insertAdjacentHTML('beforeend', `
    <tr>
      <td><input type="text" value="" id="${type}_key_${idx}" placeholder="e.g. vtv"></td>
      <td><input type="text" value="" id="${type}_val_${idx}" placeholder="pronunciation"></td>
      <td style="white-space:nowrap;">
        <button class="btn-dict-del" onclick="saveDictRow('${type}', ${idx})" title="Save">&#10003;</button>
        <button class="btn-dict-del" onclick="this.closest('tr').remove()" title="Cancel">&#10005;</button>
      </td>
    </tr>`);
	document.getElementById(`${type}_key_${idx}`)?.focus();
}

/* Modal */
newVoiceBtn.addEventListener('click', () => {
	voiceLabModal.classList.remove('hidden');
	cloneVoiceName.value = ''; cloneVoiceGender.value = 'male'; cloneVoiceDesc.value = ''; cloneRefAudio.value = ''; cloneRefAudio.type = 'text'; cloneRefAudio.type = 'file'; cloneRefText.value = '';
});
cloneCancelBtn.addEventListener('click', () => voiceLabModal.classList.add('hidden'));
cloneSaveBtn.addEventListener('click', async () => {
	const rawName = cloneVoiceName.value.trim();
	const gender = cloneVoiceGender.value;
	const description = cloneVoiceDesc.value.trim() || 'No description';
	const audioFile = cloneRefAudio.files[0];
	const refText = cloneRefText.value.trim();
	if (!rawName || !audioFile || !refText) { alert('Fill all fields and select an audio file'); return; }
	cloneSaveBtn.disabled = true; cloneSaveBtn.textContent = 'Uploading...';
	try {
		const fd = new FormData();
		fd.append('voice_id', rawName);
		fd.append('gender', gender);
		fd.append('description', description);
		fd.append('ref_text', refText);
		fd.append('ref_audio', audioFile);
		const res = await fetch(`${API_BASE}/tts/clone`, { method: 'POST', body: fd });
		if (!res.ok) throw new Error('Clone failed');
		const data = await res.json();
		voiceLabModal.classList.add('hidden');
		await loadVoices();
		CURRENT_VOICE_ID = data.voice_id;
		for (const m of ['medium', 'high', 'low']) {
			if ((ALL_VOICES[m] || []).find(v => v.id === data.voice_id)) {
				CURRENT_VOICE_MODE = m; break;
			}
		}
		updateVoiceLabel();
		showToast('Voice "' + (data.raw_name || rawName) + '" cloned');
	} catch (e) { alert('Error: ' + e.message); }
	cloneSaveBtn.disabled = false; cloneSaveBtn.textContent = 'Save Voice';
});
voiceLabModal.addEventListener('click', e => { if (e.target === voiceLabModal) voiceLabModal.classList.add('hidden'); });

function showError(msg) { errorDisplay.textContent = msg; errorDisplay.classList.remove('hidden'); }
function hideError() { errorDisplay.classList.add('hidden'); }

/* Resources modal + Download */
let RESOURCE_POLL = null;
let DL_POLL = null;

function getResourceIcon(name, size) {
	if (name === 'f5' || name === 'f5_model')
		return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:${size}px;height:${size}px;"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>`;
	if (name === 'piper')
		return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:${size}px;height:${size}px;"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`;
	return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:${size}px;height:${size}px;"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`;
}

function getResourceEngineLabel(name) {
	if (name === 'piper') return 'CPU · 15 voices';
	if (name === 'f5') return 'GPU · ~1.4GB';
	if (name === 'f5_voices') return 'Reference audio';
	if (name === 'omnivoice') return 'GPU · ~2.3GB';
	return 'GPU';
}

// ─── Resource Tab: Download ───

async function loadDownloadTab() {
	const container = document.getElementById('resourceDownloadTab');
	container.innerHTML = '<div class="no-chunks" style="padding:20px;">Loading...</div>';
	try {
		const [cat, dl] = await Promise.all([
			fetch(`${API_BASE}/tts/resource_catalog`).then(r => r.json()),
			fetch(`${API_BASE}/tts/download_progress`).then(r => r.json()),
		]);
		renderDownloadTab(cat, dl);
	} catch (e) {
		container.innerHTML = '<div class="no-chunks" style="padding:20px;color:var(--error);">Failed to load catalog. Is the server running?</div>';
	}
}

function renderDownloadTab(catalog, dlState) {
	const container = document.getElementById('resourceDownloadTab');
	container.innerHTML = catalog.map(r => {
		const dl = dlState[r.id] || { status: 'none', progress: 0, current_file: '', error: '' };
		const downloading = dl.status === 'downloading';
		const done = r.downloaded;
		const error = dl.status === 'error';

		let btnHtml = '';
		if (downloading) {
			btnHtml = `<button class="resource-btn" disabled>Downloading…</button>`;
		} else if (done) {
			btnHtml = `<button class="resource-btn" disabled style="background:var(--success);border-color:var(--success);">&#10003; Downloaded</button>`;
		} else if (error) {
			btnHtml = `<button class="resource-btn" onclick="startDownload('${r.id}')" style="background:var(--error);border-color:var(--error);">Retry</button>`;
		} else {
			btnHtml = `<button class="resource-btn" onclick="startDownload('${r.id}')">Download (${r.total_size_mb}MB)</button>`;
		}

		let progressHtml = '';
		if (downloading) {
			progressHtml = `
        <div class="resource-progress-wrap">
          <div class="resource-progress-bg"><div class="resource-progress-fill" style="width:${dl.progress}%"></div></div>
          <div class="resource-progress-msg">${dl.current_file ? escapeHtml(dl.current_file.split('/').pop()) : 'Starting...'} — ${dl.progress}%</div>
        </div>`;
		} else if (error && dl.error) {
			progressHtml = `<div class="resource-progress-msg" style="color:var(--error);">${escapeHtml(dl.error)}</div>`;
		}

		const cardClass = done ? 'loaded' : downloading ? 'loading' : error ? 'error' : '';
		return `<div class="resource-card ${cardClass}">
      ${getResourceIcon(r.id, 22)}
      <div class="resource-info">
        <div class="resource-name">${escapeHtml(r.label)}</div>
        <div class="resource-engine">${getResourceEngineLabel(r.id)} · ${r.total_size_mb}MB · ${r.existing_files}/${r.total_files} files</div>
        ${progressHtml}
      </div>
      ${btnHtml}
    </div>`;
	}).join('') || '<div class="no-chunks">No resources available</div>';
}

// ─── Resource Tab: Load Models ───

async function loadModelsTab() {
	const container = document.getElementById('resourceModelsTab');
	container.innerHTML = '<div class="no-chunks" style="padding:20px;">Loading...</div>';
	try {
		const res = await fetch(`${API_BASE}/tts/model_status`);
		const data = await res.json();
		renderModelsTab(data);
	} catch (e) {
		container.innerHTML = '<div class="no-chunks" style="padding:20px;color:var(--error);">Failed to fetch model status.</div>';
	}
}

function renderModelsTab(ms) {
	const container = document.getElementById('resourceModelsTab');
	const engines = [
		{ key: 'f5', label: 'F5-TTS (Medium · GPU)', state: (ms && ms.f5) || {} },
		{ key: 'omnivoice', label: 'OmniVoice (High · GPU)', state: (ms && ms.omnivoice) || {} },
	];

	container.innerHTML = engines.map(e => {
		const s = e.state;
		const loaded = s.loaded;
		const loading = s.loading;
		const progress = s.progress || 0;
		const message = s.message || '';
		const error = s.error;
		const cardClass = loaded ? 'loaded' : loading ? 'loading' : error ? 'error' : '';
		const badgeClass = loaded ? 'loaded' : loading ? 'loading' : error ? 'unloaded' : 'unloaded';
		const badgeText = loaded ? 'Loaded ✓' : loading ? 'Loading...' : error ? 'Error' : 'Not Loaded';
		const btnDisabled = loaded || loading;
		const btnText = loaded ? 'Loaded' : loading ? 'Loading...' : 'Load Model';

		let progHtml = '';
		if (loading) {
			progHtml = `<div class="resource-progress-wrap"><div class="resource-progress-bg"><div class="resource-progress-fill" style="width:${progress}%"></div></div><div class="resource-progress-msg">${escapeHtml(message)}</div></div>`;
		} else if (error && message) {
			progHtml = `<div class="resource-progress-msg" style="color:var(--error);">${escapeHtml(message)}</div>`;
		}

		return `<div class="resource-card ${cardClass}">
      ${getResourceIcon(e.key === 'f5' ? 'f5' : 'omnivoice', 22)}
      <div class="resource-info">
        <div class="resource-name">${e.label}</div>
        <span class="resource-status-badge ${badgeClass}">${badgeText}</span>
        ${progHtml}
      </div>
      <button class="resource-btn" ${btnDisabled ? 'disabled' : ''} onclick="loadModel('${e.key}')">${btnText}</button>
    </div>`;
	}).join('');
}

// ─── Tab Switching ───

function switchResourceTab(tab) {
	document.querySelectorAll('#resourceModal .dict-tab').forEach(b => b.classList.toggle('active', b.dataset.rtab === tab));
	document.getElementById('resourceDownloadTab').classList.toggle('hidden', tab !== 'download');
	document.getElementById('resourceModelsTab').classList.toggle('hidden', tab !== 'models');
	if (tab === 'download') loadDownloadTab();
	else loadModelsTab();
}

// ─── Download Actions ───

async function startDownload(rid) {
	try {
		const res = await fetch(`${API_BASE}/tts/start_download`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ resource_id: rid }),
		});
		if (!res.ok) throw new Error('Start failed');
		startDownloadPoll();
	} catch (e) {
		showToast('Download: ' + e.message);
	}
}

function startDownloadPoll() {
	stopDownloadPoll();
	DL_POLL = setInterval(async () => {
		try {
			const [cat, dl] = await Promise.all([
				fetch(`${API_BASE}/tts/resource_catalog`).then(r => r.json()),
				fetch(`${API_BASE}/tts/download_progress`).then(r => r.json()),
			]);
			renderDownloadTab(cat, dl);
			const allDone = Object.values(dl).every(s => s.status !== 'downloading');
			if (allDone) {
				stopDownloadPoll();
				if (Object.values(dl).some(s => s.status === 'done')) {
					await loadVoices();
					showToast('Download complete!');
				}
			}
		} catch (e) { stopDownloadPoll(); }
	}, 1000);
}

function stopDownloadPoll() {
	if (DL_POLL) { clearInterval(DL_POLL); DL_POLL = null; }
}

// ─── Model Load ───

async function loadModel(model) {
	try {
		const res = await fetch(`${API_BASE}/tts/load_model`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ model }),
		});
		if (!res.ok) {
			const d = await res.json().catch(() => ({}));
			showToast('Error: ' + (d.detail || ('Load failed (' + res.status + ')')));
			loadModelsTab();
			return;
		}
		const data = await res.json();
		if (data.status === 'already_loaded') {
			loadModelsTab();
			return;
		}
		startModelPoll();
	} catch (e) {
		showToast('Error: ' + e.message);
		loadModelsTab();
	}
}

function startModelPoll() {
	stopModelPoll();
	RESOURCE_POLL = setInterval(async () => {
		try {
			const res = await fetch(`${API_BASE}/tts/model_status`);
			const data = await res.json();
			renderModelsTab(data);
			if (!data.f5.loading && !data.omnivoice.loading) {
				stopModelPoll();
				if (data.f5.loaded || data.omnivoice.loaded) {
					await loadVoices();
					showToast('Model loaded!');
				}
			}
		} catch (e) { stopModelPoll(); }
	}, 800);
}

function stopModelPoll() {
	if (RESOURCE_POLL) { clearInterval(RESOURCE_POLL); RESOURCE_POLL = null; }
}

// ─── Open / Close ───

async function openResourceModal() {
	stopDownloadPoll(); stopModelPoll();
	document.getElementById('resourceModal').classList.remove('hidden');
	// Activate download tab by default
	const active = document.querySelector('#resourceModal .dict-tab.active');
	switchResourceTab(active ? active.dataset.rtab : 'download');
}

document.getElementById('resourceBtn').addEventListener('click', openResourceModal);
document.getElementById('resourceCloseBtn').addEventListener('click', () => {
	stopDownloadPoll(); stopModelPoll();
	document.getElementById('resourceModal').classList.add('hidden');
});
document.getElementById('resourceModal').addEventListener('click', e => {
	if (e.target === e.currentTarget) {
		stopDownloadPoll(); stopModelPoll();
		document.getElementById('resourceModal').classList.add('hidden');
	}
});

// Help modal
// Theme toggle
const savedTheme = localStorage.getItem('capcap_theme') || '';
if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme);
document.getElementById('themeBtn').addEventListener('click', () => {
	const html = document.documentElement;
	const isDark = html.getAttribute('data-theme') === 'dark';
	if (isDark) { html.removeAttribute('data-theme'); localStorage.setItem('capcap_theme', ''); }
	else { html.setAttribute('data-theme', 'dark'); localStorage.setItem('capcap_theme', 'dark'); }
});

document.getElementById('helpBtn').addEventListener('click', () => {
	document.getElementById('helpModal').classList.remove('hidden');
});
document.getElementById('helpCloseBtn').addEventListener('click', () => {
	document.getElementById('helpModal').classList.add('hidden');
});
document.getElementById('helpModal').addEventListener('click', e => {
	if (e.target === e.currentTarget) document.getElementById('helpModal').classList.add('hidden');
});

// Keyboard shortcuts
document.addEventListener('keydown', e => {
	// Don't trigger shortcuts when typing in inputs
	if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

	if (e.code === 'Space') {
		e.preventDefault();
		if (audioPlayer.src) {
			audioPlayer.paused ? audioPlayer.play().catch(() => { }) : audioPlayer.pause();
		}
	} else if (e.key === 'r' || e.key === 'R') {
		// Retry first failed segment
		const failed = CHUNK_DATA.find(c => c.status === 'error');
		if (failed) retryChunk(failed.index);
	} else if (e.ctrlKey && e.key === 'Enter') {
		e.preventDefault();
		if (STATE === 'idle' || STATE === 'done' || STATE === 'error') {
			startGeneration();
		}
	}
});

updateCharCount(); updateSplitCheckbox(); updateModelConfig(); loadVoices();
fetch(`${API_BASE}/tts/pause_config`).then(r => r.json()).then(c => { PAUSE_CFG = c; updateCharCount(); }).catch(() => { });

// ============ Video Download & Library (MVP-1) ============
let VIDEO_DL_POLL = null;
let VIDEO_TR_POLL = null;
let VIDEO_TL_POLL = null;
let VIDEO_DB_POLL = null;
let VIDEO_CURRENT_ID = null;
let VIDEO_DUB_SELECTED = null;
let CURRENT_WORKING_SOURCE = 'original'; // 'original' | 'dub' | 'export'
let CURRENT_DUB_ID = null;
let CURRENT_EXPORT_ID = null;
let WORKSPACE_DUBS = [];
let WORKSPACE_EXPORTS = [];
let WORKSPACE_ORIG_SEGMENTS = [];
let WORKSPACE_VI_SEGMENTS = [];
let WORKSPACE_CURRENT_SEGMENT = -1;
let SEGMENT_EDITOR_LOADED = false;

let COMPOSE_MASKS = [];
let COMPOSE_MASK_SELECTED = null;
let COMPOSE_SUBS_ENABLED = false;
let COMPOSE_STATE_INIT = false;

function videoEsc(s) {
	return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function hasSignificantChinese(text, ratio) {
	const t = String(text || '').replace(/\s+/g, '');
	if (!t.length) return false;
	const limit = (ratio !== undefined && ratio > 0 && ratio <= 1) ? ratio : 0.3;
	let cjk = 0;
	for (const ch of t) {
		const c = ch.codePointAt(0);
		if ((c >= 0x4e00 && c <= 0x9fff) || (c >= 0x3400 && c <= 0x4dbf)) cjk++;
	}
	return (cjk / t.length) >= limit;
}

function videoFmtDur(sec) {
	if (sec === null || sec === undefined || isNaN(sec) || sec <= 0) return '—';
	const m = Math.floor(sec / 60), s = Math.round(sec % 60);
	return `${m}:${String(s).padStart(2, '0')}`;
}

function videoFmtTime(iso) {
	if (!iso) return '';
	try { return new Date(iso).toLocaleString('vi-VN', { dateStyle: 'short', timeStyle: 'short' }); }
	catch (e) { return iso; }
}

const VIDEO_STATUS_LABELS = {
	downloaded: 'Đã tải',
	transcribing: 'Đang transcribe',
	transcribed: 'Đã transcribe',
	queued: 'Chờ',
	running: 'Đang chạy',
	done: 'Xong',
	failed: 'Lỗi',
};

function videoStatusBadge(st) {
	const label = VIDEO_STATUS_LABELS[st] || st || '';
	return `<span class="video-status-badge video-status-${st || ''}">${videoEsc(label)}</span>`;
}

function videoShowError(msg) {
	const el = document.getElementById('videoError');
	el.textContent = msg;
	el.classList.remove('hidden');
}

function videoClearError() {
	document.getElementById('videoError').classList.add('hidden');
}

function videoShowJob(job) {
	const wrap = document.getElementById('videoJobStatus');
	const fill = document.getElementById('videoJobFill');
	const label = document.getElementById('videoJobLabel');
	wrap.style.display = 'block';
	fill.style.width = `${Math.max(0, Math.min(100, job.progress || 0))}%`;
	let msg = job.message || job.status || '...';
	if (job.strategy || job.parsed) {
		const p = job.parsed || {};
		const bits = [job.strategy, p.platform, p.mode, p.video_id ? `id=${p.video_id}` : ''].filter(Boolean);
		if (bits.length) msg = `[${bits.join(' · ')}] ${msg}`;
	}
	label.textContent = msg;
}

function videoHideJob() {
	document.getElementById('videoJobStatus').style.display = 'none';
}

async function videoLoadLibrary() {
	try {
		const res = await fetch(`${API_BASE}/api/videos`);
		if (!res.ok) throw new Error('Không tải được library');
		const videos = await res.json();
		videoRenderLibrary(videos);
	} catch (e) {
		videoShowError('Không tải được video library: ' + e.message);
	}
}

function videoRenderLibrary(videos) {
	const list = document.getElementById('videoLibraryList');
	document.getElementById('videoLibraryCount').textContent = videos.length ? `(${videos.length})` : '';
	if (!videos.length) {
		list.innerHTML = '<div class="no-chunks">Chưa có video nào. Dán link video ở card bên trái và bấm Download.</div>';
		return;
	}
	list.innerHTML = videos.map(v => {
		const hasTr = v.transcript_path || v.status === 'transcribed';
		return `<div class="video-item" id="videoItem_${videoEsc(v.id)}">
			<div class="video-item-title" title="${videoEsc(v.title)}">${videoEsc(v.title || v.id)}</div>
			<div class="video-item-meta">
				<span>${videoEsc(v.platform || '?')}</span>
				<span>${videoFmtDur(v.duration)}</span>
				<span>${videoFmtTime(v.created_at)}</span>
			</div>
			<div class="video-item-meta">${videoStatusBadge(v.status)}<span class="video-item-progress" id="videoItemProg_${videoEsc(v.id)}" style="display:none;"></span></div>
			<div class="video-item-actions">
				<button class="btn-sm" data-action="select" data-id="${videoEsc(v.id)}">Select</button>
				<button class="btn-sm" data-action="transcribe" data-id="${videoEsc(v.id)}" ${v.status === 'transcribing' ? 'disabled' : ''}>Transcribe</button>
				<button class="btn-sm" data-action="tts" data-id="${videoEsc(v.id)}" ${hasTr ? '' : 'disabled'} title="${hasTr ? '' : 'Cần transcribe trước'}">Send to TTS</button>
				<button class="btn-sm" data-action="open" data-id="${videoEsc(v.id)}">Open Folder</button>
				<button class="btn-sm" data-action="delete" data-id="${videoEsc(v.id)}" style="color:var(--error);">Delete</button>
			</div>
		</div>`;
	}).join('');

	list.querySelectorAll('button[data-action]').forEach(btn => {
		btn.addEventListener('click', () => {
			const { action, id } = btn.dataset;
			if (action === 'select') videoOpenDetail(id);
			else if (action === 'transcribe') videoTranscribe(id);
			else if (action === 'tts') videoSendToTTS(id);
			else if (action === 'open') videoOpenFolder(id);
			else if (action === 'delete') videoDelete(id);
		});
	});
}

function videoDetectUrl() {
	const url = document.getElementById('videoUrlInput').value.trim();
	const badge = document.getElementById('videoUrlBadge');
	const info = document.getElementById('videoUrlInfo');
	const maxEl = document.getElementById('videoMaxItems');
	const empty = () => { badge.style.display = 'none'; info.style.display = 'none'; };
	if (!url) { empty(); return; }
	const setBadge = (text, kind, detail, single) => {
		badge.textContent = text;
		badge.className = 'video-url-badge ' + (kind || '');
		badge.style.display = 'block';
		if (detail) { info.textContent = detail; info.style.display = 'block'; } else { info.style.display = 'none'; }
		maxEl.disabled = !!single;
		if (single) maxEl.value = '1';
	};
	const mModal = url.match(/[?&]modal_id=(\d+)/);
	if (/douyin\.com/i.test(url)) {
		if (/v\.douyin\.com/i.test(url)) { setBadge('Douyin short link', 'badge-douyin', 'Sẽ resolve redirect khi tải — tải ĐÚNG video. max_items bỏ qua.', true); return; }
		const mVideo = url.match(/douyin\.com\/(?:video|note)\/([A-Za-z0-9_-]+)/i);
		if (mModal) { setBadge(`Will download video ID ${mModal[1]} (modal_id)`, 'badge-douyin', 'Douyin jingxuan modal — tải ĐÚNG video, không quét feed.', true); return; }
		if (mVideo) { setBadge(`Will download video ID ${mVideo[1]}`, 'badge-douyin', 'Douyin direct video — tải ĐÚNG video này.', true); return; }
		if (/jingxuan/i.test(url)) { setBadge('Douyin jingxuan feed', 'badge-douyin', 'Không có modal_id — tải N video đầu feed theo max_items.', false); return; }
		if (/\/user\//i.test(url)) { setBadge('Douyin user profile', 'badge-douyin', 'Tải video của user theo max_items.', false); return; }
		setBadge('Douyin link (chưa nhận dạng)', 'badge-douyin', 'Thử tải single. Nếu là feed, dùng link /jingxuan hoặc /user/.', false);
		return;
	}
	const mYt = url.match(/(?:youtube\.com|youtu\.be)\/.*(?:v=|shorts\/|embed\/)?([A-Za-z0-9_-]{6,})/i);
	if (/youtube\.com|youtu\.be/i.test(url)) {
		if (/playlist|list=/i.test(url)) { setBadge('YouTube playlist — tải theo max_items', 'badge-yt', 'yt-dlp playlist, giới hạn max_items ≤ 20.', false); return; }
		setBadge('YouTube video — tải qua yt-dlp', 'badge-yt', mYt ? `video id: ${mYt[1]}` : 'Bấm Download để tải.', true);
		return;
	}
	if (/tiktok\.com/i.test(url)) {
		if (/\/@/i.test(url) && !/\/video\//i.test(url)) { setBadge('TikTok user — tải theo max_items', 'badge-tt', 'yt-dlp user feed, giới hạn max_items ≤ 20.', false); return; }
		setBadge('TikTok video — tải qua yt-dlp', 'badge-tt', 'vt.tiktok.com cũng OK.', true);
		return;
	}
	if (/facebook\.com|fb\.watch/i.test(url)) {
		setBadge('Facebook video — tải qua yt-dlp', 'badge-fb', 'Video public mới tải được (không cookies/login).', true);
		return;
	}
	if (/bilibili\.com|b23\.tv/i.test(url)) {
		const mBv = url.match(/\/video\/(BV[0-9A-Za-z]+)/i);
		if (/space\.bilibili\.com|medialist/i.test(url)) {
			setBadge('Bilibili space/medialist — tải theo max_items', 'badge-bl', 'yt-dlp, giới hạn max_items ≤ 20.', false);
		} else {
			setBadge('Bilibili video — tải qua yt-dlp', 'badge-bl', mBv ? `BV id: ${mBv[1]}` : 'b23.tv sẽ resolve bởi yt-dlp.', true);
		}
		return;
	}
	if (url.startsWith('http')) {
		setBadge('Nền tảng khác — thử yt-dlp', 'badge-other', 'yt-dlp tự chọn extractor. Không hỗ trợ login/cookies.', true);
	} else {
		setBadge('URL không hợp lệ', 'badge-err', 'Chỉ chấp nhận link http/https.', true);
	}
}

async function videoStartDownload() {
	videoClearError();
	const url = document.getElementById('videoUrlInput').value.trim();
	if (!url) { videoShowError('Hãy dán link video (Douyin / YouTube / TikTok / Facebook).'); return; }
	const sourceType = document.getElementById('videoSourceSelect').value;
	const strategy = document.getElementById('videoStrategySelect').value;
	const maxItems = Math.max(1, Math.min(20, parseInt(document.getElementById('videoMaxItems').value, 10) || 1));
	const headless = document.getElementById('videoHeadless').checked;

	const btn = document.getElementById('videoDownloadBtn');
	btn.disabled = true;
	videoShowJob({ progress: 0, message: 'Đang gửi yêu cầu...' });
	try {
		const res = await fetch(`${API_BASE}/api/videos/download`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ url, source_type: sourceType, max_items: maxItems, headless, download_strategy: strategy }),
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) {
			videoShowError(data.detail || `Lỗi ${res.status}`);
			videoHideJob();
			return;
		}
		videoPollDownload(data.job_id);
	} catch (e) {
		videoShowError('Không kết nối được server: ' + e.message);
		videoHideJob();
	}
}

function videoPollDownload(jobId) {
	if (VIDEO_DL_POLL) clearTimeout(VIDEO_DL_POLL);
	const tick = async () => {
		try {
			const res = await fetch(`${API_BASE}/api/videos/download/${jobId}`);
			if (!res.ok) { videoShowJob({ progress: 0, message: 'Job không tồn tại' }); return; }
			const job = await res.json();
			videoShowJob(job);
			if (job.status === 'done') {
				videoShowJob({ ...job, message: `${job.message || 'Xong'} — đã cập nhật thư viện.` });
				document.getElementById('videoDownloadBtn').disabled = false;
				videoLoadLibrary();
				return;
			}
			if (job.status === 'failed') {
				videoShowError(job.error || job.message || 'Download thất bại.');
				videoShowDiagnostics(job.diagnostics);
				document.getElementById('videoDownloadBtn').disabled = false;
				return;
			}
			VIDEO_DL_POLL = setTimeout(tick, 1000);
		} catch (e) {
			VIDEO_DL_POLL = setTimeout(tick, 1500);
		}
	};
	tick();
}

function videoShowDiagnostics(diag) {
	const box = document.getElementById('videoDiagnostics');
	if (!diag || !diag.steps || !diag.steps.length) { box.classList.add('hidden'); return; }
	const lines = [`Kết luận: ${diag.conclusion || 'unknown'}`];
	if (diag.error) lines.push(`Error: ${diag.error}`);
	for (const s of diag.steps) {
		lines.push(`${s.ok ? 'OK ' : 'FAIL'} | ${s.stage} | ${s.detail || ''}`);
		for (const extra of ['status', 'content_type', 'final_domain', 'url']) {
			if (s[extra] !== undefined && s[extra] !== '') lines.push(`      ${extra}: ${s[extra]}`);
		}
	}
	for (const [vid, d] of Object.entries(diag.cdn || {})) {
		for (const a of (d.attempts || [])) {
			lines.push(`CDN ${vid} | attempt ${a.attempt} | ${a.label}: status=${a.status ?? '-'} ct=${a.content_type || '-'} len=${a.content_length || '-'} ${a.error ? 'err=' + a.error : ''}`);
			if ((a.redirects || []).length) lines.push(`      redirects: ${a.redirects.length} → ${a.final_domain || ''}`);
		}
	}
	box.innerHTML = `<details><summary>Diagnostics (Details)</summary><pre>${videoEsc(lines.join('\n'))}</pre></details>`;
	box.classList.remove('hidden');
}

async function videoTranscribe(id) {
	videoClearError();
	const btn = document.querySelector(`#videoItem_${CSS.escape(id)} button[data-action="transcribe"]`);
	if (btn) btn.disabled = true;
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/transcribe`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ model: 'base' }),
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) { videoShowError(data.detail || `Lỗi ${res.status}`); return; }
		videoPollTranscribe(data.job_id, id);
	} catch (e) {
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

function videoPollTranscribe(jobId, videoId) {
	if (VIDEO_TR_POLL) clearTimeout(VIDEO_TR_POLL);
	const prog = document.getElementById(`videoItemProg_${CSS.escape(videoId)}`);
	const tick = async () => {
		try {
			const res = await fetch(`${API_BASE}/api/videos/transcribe/${jobId}`);
			if (!res.ok) return;
			const job = await res.json();
			if (prog) { prog.style.display = 'inline'; prog.textContent = ` ${job.progress || 0}%`; }
			if (job.status === 'done' || job.status === 'failed') {
				videoLoadLibrary();
				if (job.status === 'failed') videoShowError(job.error || job.message || 'Transcribe thất bại.');
				if (VIDEO_CURRENT_ID === videoId) videoOpenDetail(videoId);
				return;
			}
			VIDEO_TR_POLL = setTimeout(tick, 1000);
		} catch (e) {
			VIDEO_TR_POLL = setTimeout(tick, 1500);
		}
	};
	tick();
}

async function videoOpenDetail(id) {
	videoClearError();
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}`);
		if (!res.ok) throw new Error('Không tải được video');
		const v = await res.json();
		VIDEO_CURRENT_ID = v.id;
		document.getElementById('videoDetailCard').classList.remove('hidden');
		document.getElementById('videoDetailTitle').textContent = v.title || v.id;
		const preview = document.getElementById('videoPreview');
		preview.src = `${API_BASE}/api/videos/${encodeURIComponent(v.id)}/media`;
		preview.setAttribute('data-ws-source', 'original:original');
		preview.load();

		const hasTranscript = (v.transcript && v.transcript.text) || v.status === 'transcribed';
		const hasVi = !!(v.transcript_vi_text || (v.transcript_vi && v.transcript_vi.text));
		document.getElementById('videoUseTranscriptBtn').disabled = !hasTranscript;
		document.getElementById('videoTranslateBtn').disabled = !hasTranscript;
		document.getElementById('videoUseViTranscriptBtn').disabled = !hasVi;
		document.getElementById('videoTtsNotice').style.display = 'none';
		document.getElementById('videoTtsViNotice').style.display = 'none';
		document.getElementById('videoTranscriptText').textContent = hasTranscript
			? (v.transcript && v.transcript.text) || 'Đã có transcript (xem chi tiết khi tải lại).'
			: 'Chưa có transcript — bấm Transcribe ở Video Library.';
		document.getElementById('videoTranscriptViText').textContent = hasVi
			? (v.transcript_vi_text || (v.transcript_vi && v.transcript_vi.text))
			: 'Chưa có bản dịch — bấm "Translate to Vietnamese".';

		const tlStatus = v.translation_status || 'none';
		const tlStatusEl = document.getElementById('videoTranslateStatus');
		const tlLabelMap = {
			none: '',
			translating: 'Đang dịch sang tiếng Việt…',
			translated: 'Đã dịch sang tiếng Việt.',
			failed: 'Dịch thất bại — thử lại.',
		};
		if (tlStatus === 'translating') {
			tlStatusEl.style.color = 'var(--warning)';
		} else if (tlStatus === 'failed') {
			tlStatusEl.style.color = 'var(--error)';
		} else {
			tlStatusEl.style.color = 'var(--success)';
		}
		tlStatusEl.textContent = tlLabelMap[tlStatus] || '';
		tlStatusEl.style.display = tlLabelMap[tlStatus] ? 'inline' : 'none';

		const metaRows = [
			['Tiêu đề', v.title || '—'],
			['Tác giả', v.author || '—'],
			['Nền tảng', v.platform || '—'],
			['ID', v.id],
			['Đường dẫn', v.video_path || '—'],
			['Thời lượng', videoFmtDur(v.duration)],
			['Tạo lúc', videoFmtTime(v.created_at)],
			['Trạng thái', VIDEO_STATUS_LABELS[v.status] || v.status],
			['Ngôn ngữ transcript', v.transcript_language || '—'],
			['Trạng thái dịch', tlLabelMap[tlStatus] || 'Chưa dịch'],
		];
		document.getElementById('videoDetailMeta').innerHTML = '<div class="video-meta-grid">'
			+ metaRows.map(([k, val]) => `<div class="video-meta-row"><span class="video-meta-key">${videoEsc(k)}</span><span class="video-meta-val">${videoEsc(String(val))}</span></div>`).join('')
			+ '</div>';

		videoRenderDubState(v.id, v);
		VIDEO_DUB_SELECTED = null;
		videoRenderDubs(v.id);

		// MVP-8.4: populate subtitle/transcript segment arrays for compose preview
		window.VIDEO_VI_SEGMENTS = (v.transcript_vi && v.transcript_vi.segments) || null;
		window.VIDEO_TRANSCRIPT_SEGMENTS = (v.transcript && v.transcript.segments) || null;

		// MVP-8.5: workspace source state + exports + segment editor
		workspaceClear();
		workspaceLoadExports(v.id);
		workspaceLoadSegments(v.id);
		videoRenderDubs(v.id); // re-render to rebuild source select with exports + dubs

		videoComposeReset();
		videoComposeSyncOverlay();
		videoComposeResetOverlay();
		videoComposeRefreshButtons();
		workspaceUpdateExportSummary();
		videoEditorInit();
	} catch (e) {
		videoShowError('Không mở được video: ' + e.message);
	}
}

function videoRenderDubState(id, v) {
	const voiceSel = document.getElementById('dubVoiceSelect');
	const lowVoices = (ALL_VOICES && ALL_VOICES.low) || [];
	const current = voiceSel.value || 'banmai';
	voiceSel.innerHTML = '';
	const voiceList = lowVoices.length ? lowVoices : [{ id: 'banmai', label: 'Ban Mai' }];
	for (const vo of voiceList) {
		const opt = document.createElement('option');
		opt.value = vo.id;
		opt.textContent = vo.label || vo.id;
		if (vo.id === current) opt.selected = true;
		voiceSel.appendChild(opt);
	}
	const hasVi = !!(v.transcript_vi_text || (v.transcript_vi && v.transcript_vi.text));
	const dubStatus = v.dub_status || 'none';
	const statusEl = document.getElementById('videoDubStatus');
	const dubBtn = document.getElementById('videoDubBtn');
	const previewBtn = document.getElementById('videoPreviewDubBtn');
	const folderBtn = document.getElementById('videoOpenDubFolderBtn');
	if (dubStatus === 'dubbing') {
		statusEl.style.color = 'var(--warning)';
		statusEl.textContent = 'Đang lồng tiếng…';
		dubBtn.disabled = true;
		previewBtn.disabled = true;
		folderBtn.disabled = true;
	} else if (dubStatus === 'done') {
		statusEl.style.color = 'var(--success)';
		statusEl.textContent = 'Đã lồng tiếng xong — xem trong Dubbed Outputs bên dưới.';
		dubBtn.disabled = false;
		previewBtn.disabled = false;
		folderBtn.disabled = false;
	} else if (dubStatus === 'failed') {
		statusEl.style.color = 'var(--error)';
		statusEl.textContent = v.dub_error ? ('Dub thất bại: ' + v.dub_error) : 'Dub thất bại — thử lại.';
		dubBtn.disabled = false;
		previewBtn.disabled = false;
		folderBtn.disabled = false;
	} else {
		statusEl.style.color = 'var(--text-muted)';
		statusEl.textContent = '';
		dubBtn.disabled = !hasVi;
		previewBtn.disabled = true;
		folderBtn.disabled = true;
	}
}

function onDubSlider(sliderId, labelId) {
	const el = document.getElementById(sliderId);
	const val = parseInt(el.value, 10) || 0;
	const label = document.getElementById(labelId);
	if (sliderId === 'dubMaxSpeed') label.textContent = (val / 100).toFixed(2) + 'x';
	else label.textContent = val + '%';
}

function onDubModeChange() {
	const mode = document.getElementById('dubAudioMode').value;
	const ov = document.getElementById('dubOriginalVolume');
	ov.disabled = mode !== 'mix';
}

function applyDubPreset(kind) {
	const mode = document.getElementById('dubAudioMode');
	const ov = document.getElementById('dubOriginalVolume');
	const dv = document.getElementById('dubDubVolume');
	if (kind === 'almost') { mode.value = 'mix'; ov.value = 3; dv.value = 120; }
	else if (kind === 'mute') { mode.value = 'replace'; ov.value = 0; dv.value = 120; }
	else if (kind === 'quiet') { mode.value = 'mix'; ov.value = 5; dv.value = 120; }
	else if (kind === 'remove') { mode.value = 'replace'; ov.value = 0; dv.value = 120; }
	else { mode.value = 'mix'; ov.value = 35; dv.value = 120; }
	onDubSlider('dubOriginalVolume', 'dubOriginalVolumeVal');
	onDubSlider('dubDubVolume', 'dubDubVolumeVal');
	onDubModeChange();
}

function videoFmtSize(bytes) {
	if (bytes === undefined || bytes === null) return '—';
	if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
	if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB';
	return bytes + ' B';
}

async function videoRenderDubs(id) {
	const container = document.getElementById('videoDubOutputs');
	const countEl = document.getElementById('videoDubOutputsCount');
	const planEl = document.getElementById('videoDubPlanText');
	planEl.style.display = 'none';
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs`);
		if (!res.ok) throw new Error(`Lỗi ${res.status}`);
		const items = await res.json();
		countEl.textContent = items.length ? `${items.length} output` : '';
		WORKSPACE_DUBS = items;
		if (!items.length) {
			container.innerHTML = '<div style="font-size:11px;color:var(--text-muted);">Chưa có dub output nào — tạo ở trên.</div>';
			workspaceRenderSourceSelect();
			return;
		}
		const modeLabel = { mix: 'Mix', replace: 'Replace', dub_only: 'Dub only', original_only: 'Original only' };
		container.innerHTML = items.map(m => {
			const sel = m.dub_id === VIDEO_DUB_SELECTED;
			return '<div style="border:1px solid ' + (sel ? 'var(--success)' : 'var(--card-border)') + ';border-radius:var(--radius-sm);padding:8px 10px;background:var(--card-bg);">'
				+ '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">'
				+ '<div style="font-size:12px;font-weight:600;color:var(--text-secondary);">' + videoEsc(m.dub_id) + '</div>'
				+ '<div style="font-size:11px;color:var(--text-muted);">' + videoFmtTime(m.created_at) + ' · ' + (modeLabel[m.audio_mode] || m.audio_mode)
				+ ' · orig ' + Math.round((m.original_volume || 0) * 100) + '% · dub ' + Math.round((m.dub_volume || 1) * 100) + '%'
				+ ' · ' + videoEsc(m.voice_id || '') + ' · burn ' + (m.burn_subtitles ? 'yes' : 'no')
				+ ' · ' + (m.warnings_count || 0) + ' warnings · ' + videoFmtSize(m.size_bytes) + '</div></div>'
				+ '<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">'
				+ `<button class="btn-sm" onclick="videoDubCardAction('${videoEsc(id)}','${videoEsc(m.dub_id)}','preview')">Preview</button>`
				+ `<button class="btn-sm" onclick="videoDubCardAction('${videoEsc(id)}','${videoEsc(m.dub_id)}','open')">Open Folder</button>`
				+ `<a class="btn-sm" href="${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(m.dub_id)}/media" download style="text-decoration:none;">Download MP4</a>`
				+ `<button class="btn-sm" onclick="videoDubCardAction('${videoEsc(id)}','${videoEsc(m.dub_id)}','plan')">View Plan</button>`
				+ `<button class="btn-sm" onclick="videoDubCardAction('${videoEsc(id)}','${videoEsc(m.dub_id)}','delete')" style="color:var(--error);">Delete</button>`
				+ '</div></div>';
		}).join('');
		workspaceRenderSourceSelect();
	} catch (e) {
		container.innerHTML = '<div style="font-size:11px;color:var(--error);">Không tải được danh sách output: ' + videoEsc(e.message) + '</div>';
	}
}

async function videoDubCardAction(id, dubId, action) {
	videoClearError();
	if (action === 'preview') {
		VIDEO_DUB_SELECTED = dubId;
		CURRENT_WORKING_SOURCE = 'dub';
		CURRENT_DUB_ID = dubId;
		CURRENT_EXPORT_ID = null;
		const preview = document.getElementById('videoPreview');
		preview.src = `${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(dubId)}/media`;
		preview.setAttribute('data-ws-source', 'dub:' + dubId);
		preview.load();
		preview.play().catch(() => { });
		const statusEl = document.getElementById('videoDubStatus');
		statusEl.style.color = 'var(--success)';
		statusEl.textContent = 'Đang xem output ' + dubId + ' (output.mp4).';
		workspaceSyncSourceUI();
		workspaceRenderSourceSelect();
		videoRenderDubs(id);
		videoComposeSyncOverlay();
		videoComposeRenderSubtitle();
		workspaceUpdateExportSummary();
		return;
	}
	if (action === 'open') {
		try {
			const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(dubId)}/open-folder`, { method: 'POST' });
			const data = await res.json().catch(() => ({}));
			if (!res.ok) videoShowError(data.detail || `Lỗi ${res.status}`);
		} catch (e) {
			videoShowError('Không kết nối được server: ' + e.message);
		}
		return;
	}
	if (action === 'plan') {
		const planEl = document.getElementById('videoDubPlanText');
		try {
			const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(dubId)}`);
			if (!res.ok) throw new Error(`Lỗi ${res.status}`);
			const detail = await res.json();
			const body = {
				dub_id: detail.dub_id,
				audio_mode: detail.audio_mode,
				original_volume: detail.original_volume,
				dub_volume: detail.dub_volume,
				max_speed: detail.max_speed,
				burn_subtitles: detail.burn_subtitles,
				voice_id: detail.voice_id,
				plan_summary: detail.plan_summary,
				segments: (detail.plan_summary && detail.plan_summary.segment_count !== undefined)
					? (detail._segments_raw || ['(segments chi tiết trong dubbing_plan.json)'])
					: ['(chưa có plan)'],
			};
			planEl.textContent = JSON.stringify(detail, null, 2);
			planEl.style.display = 'block';
			planEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
		} catch (e) {
			videoShowError('Không xem được plan: ' + e.message);
		}
		return;
	}
	if (action === 'delete') {
		if (!confirm(`Xóa dub output "${dubId}"? (video gốc và transcript vẫn giữ nguyên)`)) return;
		try {
			const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(dubId)}`, { method: 'DELETE' });
			const data = await res.json().catch(() => ({}));
			if (!res.ok) { videoShowError(data.detail || `Lỗi ${res.status}`); return; }
			if (VIDEO_DUB_SELECTED === dubId) VIDEO_DUB_SELECTED = null;
			if (CURRENT_WORKING_SOURCE === 'dub' && CURRENT_DUB_ID === dubId) {
				CURRENT_WORKING_SOURCE = 'original';
				CURRENT_DUB_ID = null;
				CURRENT_EXPORT_ID = null;
				workspaceSyncSourceUI();
				workspaceRenderSourceSelect();
			}
			videoRenderDubs(id);
			workspaceUpdateExportSummary();
		} catch (e) {
			videoShowError('Không kết nối được server: ' + e.message);
		}
	}
}

async function videoAutoSelectLatestDub(id) {
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs`);
		if (!res.ok) return;
		const items = await res.json();
		if (!items.length) return;
		const latest = items[0];
		VIDEO_DUB_SELECTED = latest.dub_id;
		CURRENT_WORKING_SOURCE = 'dub';
		CURRENT_DUB_ID = latest.dub_id;
		CURRENT_EXPORT_ID = null;
		const preview = document.getElementById('videoPreview');
		preview.src = `${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(latest.dub_id)}/media`;
		preview.setAttribute('data-ws-source', 'dub:' + latest.dub_id);
		preview.load();
		preview.play().catch(() => { });
		const statusEl = document.getElementById('videoDubStatus');
		statusEl.style.color = 'var(--success)';
		statusEl.textContent = 'Đã lồng tiếng — đang xem output mới nhất: ' + latest.dub_id;
		workspaceSyncSourceUI();
		workspaceRenderSourceSelect();
		videoRenderDubs(id);
	} catch (e) { /* im lặng */ }
}

async function videoDub(id) {
	videoClearError();
	document.getElementById('videoDubBtn').disabled = true;
	const statusEl = document.getElementById('videoDubStatus');
	statusEl.style.color = 'var(--warning)';
	statusEl.textContent = 'Đang khởi tạo job dub…';
	try {
		const nameInput = document.getElementById('dubOutputName');
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/dub`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				audio_mode: document.getElementById('dubAudioMode').value,
				original_volume: (parseInt(document.getElementById('dubOriginalVolume').value, 10) || 0) / 100,
				dub_volume: (parseInt(document.getElementById('dubDubVolume').value, 10) || 120) / 100,
				max_speed: (parseInt(document.getElementById('dubMaxSpeed').value, 10) || 125) / 100,
				burn_subtitles: document.getElementById('dubBurnSubtitles').checked,
				normalize_audio: document.getElementById('dubNormalizeAudio').checked,
				voice_id: document.getElementById('dubVoiceSelect').value || 'banmai',
				output_name: (nameInput.value || '').trim() || null,
			}),
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) {
			statusEl.style.color = 'var(--error)';
			statusEl.textContent = data.detail || `Lỗi ${res.status}`;
			document.getElementById('videoDubBtn').disabled = false;
			videoShowError(data.detail || `Lỗi ${res.status}`);
			return;
		}
		statusEl.textContent = 'Đã xếp hàng job dub…';
		videoPollDub(data.job_id, id);
	} catch (e) {
		statusEl.textContent = '';
		document.getElementById('videoDubBtn').disabled = false;
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

function videoPollDub(jobId, videoId) {
	if (VIDEO_DB_POLL) clearTimeout(VIDEO_DB_POLL);
	const statusEl = document.getElementById('videoDubStatus');
	const tick = async () => {
		try {
			const res = await fetch(`${API_BASE}/api/videos/dub/${jobId}`);
			if (!res.ok) return;
			const job = await res.json();
			if (job.status === 'running' || job.status === 'queued') {
				statusEl.style.display = '';
				statusEl.style.color = 'var(--warning)';
				statusEl.textContent = `${job.progress || 0}% — ${job.message || 'Đang lồng tiếng…'}`;
				VIDEO_DB_POLL = setTimeout(tick, 1500);
				return;
			}
			if (job.status === 'done') {
				statusEl.style.color = 'var(--success)';
				statusEl.textContent = job.message || 'Đã lồng tiếng xong.';
				if (VIDEO_CURRENT_ID === videoId) {
					videoOpenDetail(videoId);
					setTimeout(() => videoAutoSelectLatestDub(videoId), 600);
				}
				return;
			}
			if (job.status === 'failed') {
				if (VIDEO_CURRENT_ID === videoId) videoOpenDetail(videoId);
				statusEl.style.color = 'var(--error)';
				statusEl.textContent = job.error || job.message || 'Dub thất bại.';
				videoShowError(job.error || job.message || 'Dub thất bại.');
				return;
			}
			VIDEO_DB_POLL = setTimeout(tick, 1500);
		} catch (e) {
			VIDEO_DB_POLL = setTimeout(tick, 1500);
		}
	};
	tick();
}

function videoPreviewDub(id) {
	videoClearError();
	const preview = document.getElementById('videoPreview');
	if (VIDEO_DUB_SELECTED) {
		CURRENT_WORKING_SOURCE = 'dub';
		CURRENT_DUB_ID = VIDEO_DUB_SELECTED;
		CURRENT_EXPORT_ID = null;
	}
	const src = VIDEO_DUB_SELECTED
		? `${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(VIDEO_DUB_SELECTED)}/media`
		: `${API_BASE}/api/videos/${encodeURIComponent(id)}/dubbed-media`;
	preview.src = src;
	preview.load();
	preview.play().catch(() => { });
	const statusEl = document.getElementById('videoDubStatus');
	statusEl.style.color = 'var(--success)';
	statusEl.textContent = VIDEO_DUB_SELECTED
		? `Đang xem output ${VIDEO_DUB_SELECTED}.`
		: 'Đang xem bản lồng tiếng mới nhất (output_dubbed.mp4).';
	if (VIDEO_DUB_SELECTED) {
		workspaceSyncSourceUI();
		workspaceRenderSourceSelect();
		workspaceUpdateExportSummary();
	}
}

async function videoOpenDubFolder(id) {
	videoClearError();
	try {
		const url = VIDEO_DUB_SELECTED
			? `${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(VIDEO_DUB_SELECTED)}/open-folder`
			: `${API_BASE}/api/videos/${encodeURIComponent(id)}/dub-open-folder`;
		const res = await fetch(url, { method: 'POST' });
		const data = await res.json().catch(() => ({}));
		if (!res.ok) videoShowError(data.detail || `Lỗi ${res.status}`);
	} catch (e) {
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

async function videoSendToTTS(id, variant) {
	videoClearError();
	const isVi = variant === 'vi';
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/send-to-tts${isVi ? '?variant=vi' : ''}`, { method: 'POST' });
		const data = await res.json().catch(() => ({}));
		if (!res.ok) { videoShowError(data.detail || `Lỗi ${res.status}`); return; }
		textInput.value = data.text || '';
		if (typeof updateCharCount === 'function') updateCharCount();
		if (isVi) {
			document.getElementById('normalizeCheckbox').checked = true;
			const notice = document.getElementById('videoTtsViNotice');
			notice.style.display = 'inline';
			setTimeout(() => { notice.style.display = 'none'; }, 4000);
		} else {
			const notice = document.getElementById('videoTtsNotice');
			notice.style.display = 'inline';
			setTimeout(() => { notice.style.display = 'none'; }, 4000);
		}
		window.scrollTo({ top: document.querySelector('.main-layout').offsetTop - 20, behavior: 'smooth' });
	} catch (e) {
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

async function videoTranslate(id) {
	videoClearError();
	document.getElementById('videoTranslateBtn').disabled = true;
	const statusEl = document.getElementById('videoTranslateStatus');
	statusEl.style.display = 'inline';
	statusEl.style.color = 'var(--warning)';
	statusEl.textContent = 'Đang khởi tạo job dịch…';
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/translate`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ source_lang: 'auto', target_lang: 'vi', provider: 'google' }),
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) {
			statusEl.style.display = 'none';
			document.getElementById('videoTranslateBtn').disabled = false;
			videoShowError(data.detail || `Lỗi ${res.status}`);
			return;
		}
		videoPollTranslate(data.job_id, id);
	} catch (e) {
		statusEl.style.display = 'none';
		document.getElementById('videoTranslateBtn').disabled = false;
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

function videoPollTranslate(jobId, videoId) {
	if (VIDEO_TL_POLL) clearTimeout(VIDEO_TL_POLL);
	const statusEl = document.getElementById('videoTranslateStatus');
	const tick = async () => {
		try {
			const res = await fetch(`${API_BASE}/api/videos/translate/${jobId}`);
			if (!res.ok) return;
			const job = await res.json();
			if (job.status === 'running' || job.status === 'queued') {
				statusEl.style.display = 'inline';
				statusEl.style.color = 'var(--warning)';
				statusEl.textContent = `${job.progress || 0}% — ${job.message || 'Đang dịch…'}`;
				VIDEO_TL_POLL = setTimeout(tick, 1000);
				return;
			}
			if (job.status === 'done') {
				statusEl.style.display = 'inline';
				statusEl.style.color = 'var(--success)';
				statusEl.textContent = job.message || 'Đã dịch xong.';
				if (VIDEO_CURRENT_ID === videoId) videoOpenDetail(videoId);
				return;
			}
			if (job.status === 'failed') {
				statusEl.style.display = 'none';
				if (VIDEO_CURRENT_ID === videoId) videoOpenDetail(videoId);
				videoShowError(job.error || job.message || 'Dịch thất bại.');
				return;
			}
			VIDEO_TL_POLL = setTimeout(tick, 1500);
		} catch (e) {
			VIDEO_TL_POLL = setTimeout(tick, 1500);
		}
	};
	tick();
}

async function videoOpenFolder(id) {
	videoClearError();
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/open-folder`, { method: 'POST' });
		const data = await res.json().catch(() => ({}));
		if (!res.ok) videoShowError(data.detail || `Lỗi ${res.status}`);
	} catch (e) {
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

async function videoDelete(id) {
	videoClearError();
	if (!confirm(`Xóa video "${id}" khỏi thư viện (gồm cả file video)?`)) return;
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}`, { method: 'DELETE' });
		if (!res.ok) { videoShowError(`Lỗi ${res.status}`); return; }
		if (VIDEO_CURRENT_ID === id) {
			VIDEO_CURRENT_ID = null;
			workspaceClear();
			document.getElementById('videoDetailCard').classList.add('hidden');
			document.getElementById('videoPreview').pause();
			document.getElementById('videoPreview').removeAttribute('src');
		}
		videoLoadLibrary();
	} catch (e) {
		videoShowError('Không kết nối được server: ' + e.message);
	}
}

/* ===== MVP-8.4: Compose / Visual editor ===== */

function videoComposeInit(id) {
	COMPOSE_STATE_INIT = true;
	videoComposeReset();
	videoComposeRender();
	videoComposeSyncOverlay();
	videoComposeResetOverlay();
}

function videoComposeReset() {
	COMPOSE_MASKS = [];
	COMPOSE_MASK_SELECTED = null;
	COMPOSE_SUBS_ENABLED = false;
	document.getElementById('composeMaskEnabled').checked = false;
	document.getElementById('composeSubsEnabled').checked = false;
	document.getElementById('composeSubsFontSize').value = 48;
	document.getElementById('composeSubsFontSizeVal').textContent = '48';
	document.getElementById('composeSubsColor').value = '#FFFFFF';
	document.getElementById('composeSubsOutlineColor').value = '#000000';
	document.getElementById('composeSubsOutlineWidth').value = 2;
	document.getElementById('composeSubsBox').checked = false;
	document.getElementById('composeSubsBoxOpacity').value = 75;
	document.getElementById('composeSubsMarginBottom').value = 60;
	document.getElementById('composeSubsPosition').value = 'bottom';
	document.getElementById('videoComposeBtn').disabled = !VIDEO_CURRENT_ID;
	document.getElementById('videoComposePreviewBtn').disabled = !VIDEO_CURRENT_ID;
}

function videoComposeNeedVi() {
	const v = VIDEO_CURRENT_ID;
	// enable export if either che or subs is on and video loaded
	return !!VIDEO_CURRENT_ID && (document.getElementById('composeMaskEnabled').checked || document.getElementById('composeSubsEnabled').checked);
}

function videoComposeToggle() {
	const on = document.getElementById('composeMaskEnabled').checked;
	document.getElementById('composeMaskAddBtn').style.display = on ? 'inline-block' : 'none';
	document.getElementById('composeMaskList').style.display = on ? 'flex' : 'none';
	document.getElementById('composeMaskEditor').style.display = 'none';
	if (on && COMPOSE_MASKS.length === 0) {
		COMPOSE_MASKS.push(videoComposeMaskNewId(null, 'Phụ đề cũ dưới màn hình', 5, 78, 90, 14, 'blur', 18, 0.85, true));
		COMPOSE_MASK_SELECTED = COMPOSE_MASKS[0].id;
	}
	videoComposeRender();
	videoComposeRefreshButtons();
}

function videoComposeSubsToggle() {
	COMPOSE_SUBS_ENABLED = document.getElementById('composeSubsEnabled').checked;
	const layer = document.getElementById('composerSubtitleOverlay');
	layer.style.display = COMPOSE_SUBS_ENABLED ? 'block' : 'none';
	videoComposeRenderSubtitle();
	videoComposeRefreshButtons();
}

function videoComposeRefreshButtons() {
	const anyOn = document.getElementById('composeMaskEnabled').checked || document.getElementById('composeSubsEnabled').checked;
	document.getElementById('videoComposeBtn').disabled = !VIDEO_CURRENT_ID || !anyOn;
	document.getElementById('videoComposePreviewBtn').disabled = !VIDEO_CURRENT_ID || !anyOn;
}

function videoComposeMaskNewId(base, label, x, y, w, h, mode, blur, boxOpacity, enabled) {
	const id = (base || 'mask_1');
	return { id, label, x, y, w, h, mode, blur, boxOpacity, enabled };
}

function videoComposeAddMask() {
	let n = 2;
	const taken = COMPOSE_MASKS.map(m => m.id);
	while (taken.includes('mask_' + n)) n++;
	COMPOSE_MASKS.push(videoComposeMaskNewId('mask_' + n, 'Vùng che ' + n, 30, 30, 30, 12, 'box', 0, 0.6, true));
	COMPOSE_MASK_SELECTED = COMPOSE_MASKS[COMPOSE_MASKS.length - 1].id;
	videoComposeRender();
	videoComposeMaskSyncForm();
	videoComposeSyncOverlay();
}

function videoComposeSelectMask(id) {
	COMPOSE_MASK_SELECTED = id;
	videoComposeRender();
	videoComposeMaskSyncForm();
	videoComposeSyncOverlay();
}

function videoComposeDupMask(id) {
	const m = COMPOSE_MASKS.find(x => x.id === id);
	if (!m) return;
	const dup = JSON.parse(JSON.stringify(m));
	dup.id = videoComposeMaskNewId(null, '', 0, 0, 0, 0, '', 0, 0, true).id;
	let n = 1;
	while (COMPOSE_MASKS.some(x => x.id === 'mask_' + n)) n++;
	dup.id = 'mask_' + n;
	dup.x = Math.min(100 - dup.w, dup.x + 8);
	dup.y = Math.min(100 - dup.h, dup.y + 8);
	dup.label = 'Vùng che ' + n;
	COMPOSE_MASKS.push(dup);
	COMPOSE_MASK_SELECTED = dup.id;
	videoComposeRender();
	videoComposeMaskSyncForm();
	videoComposeSyncOverlay();
}

function videoComposeDeleteMask(id) {
	COMPOSE_MASKS = COMPOSE_MASKS.filter(x => x.id !== id);
	if (COMPOSE_MASK_SELECTED === id) COMPOSE_MASK_SELECTED = COMPOSE_MASKS.length ? COMPOSE_MASKS[0].id : null;
	videoComposeRender();
	if (COMPOSE_MASK_SELECTED) videoComposeMaskSyncForm();
	else document.getElementById('composeMaskEditor').style.display = 'none';
	videoComposeSyncOverlay();
}

function videoComposeMaskSyncForm() {
	const m = COMPOSE_MASKS.find(x => x.id === COMPOSE_MASK_SELECTED);
	const editor = document.getElementById('composeMaskEditor');
	if (!m) { editor.style.display = 'none'; return; }
	editor.style.display = 'block';
	document.getElementById('composeMaskMode').value = m.mode;
	document.getElementById('composeMaskX').value = Math.round(m.x);
	document.getElementById('composeMaskY').value = Math.round(m.y);
	document.getElementById('composeMaskW').value = Math.round(m.w);
	document.getElementById('composeMaskH').value = Math.round(m.h);
	document.getElementById('composeMaskBlur').value = m.blur;
	document.getElementById('composeMaskBoxOpacity').value = Math.round(m.boxOpacity * 100);
}

function videoComposeMaskFormChanged() {
	if (!COMPOSE_MASK_SELECTED) return;
	const m = COMPOSE_MASKS.find(x => x.id === COMPOSE_MASK_SELECTED);
	if (!m) return;
	m.mode = document.getElementById('composeMaskMode').value;
	m.x = clampNum(document.getElementById('composeMaskX').value, 0, 100);
	m.y = clampNum(document.getElementById('composeMaskY').value, 0, 100);
	m.w = clampNum(document.getElementById('composeMaskW').value, 1, 100);
	m.h = clampNum(document.getElementById('composeMaskH').value, 1, 100);
	m.blur = clampNum(document.getElementById('composeMaskBlur').value, 0, 50);
	m.boxOpacity = clampNum(document.getElementById('composeMaskBoxOpacity').value, 0, 100) / 100;
	m.x = Math.min(m.x, 100 - m.w);
	m.y = Math.min(m.y, 100 - m.h);
	videoComposeSyncOverlay();
}

function clampNum(v, min, max) {
	const n = parseInt(v, 10);
	if (isNaN(n)) return min;
	return Math.max(min, Math.min(max, n));
}

function videoComposeRender() {
	const toggleBtn = document.getElementById('composeMaskAddBtn');
	const on = document.getElementById('composeMaskEnabled').checked;
	toggleBtn.style.display = on ? 'inline-block' : 'none';
	const list = document.getElementById('composeMaskList');
	list.style.display = on ? 'flex' : 'none';
	list.innerHTML = '';
	if (!on) {
		document.getElementById('composeMaskEditor').style.display = 'none';
		return;
	}
	for (const m of COMPOSE_MASKS) {
		const sel = m.id === COMPOSE_MASK_SELECTED;
		const modeLabel = m.mode === 'box' ? 'Hộp che' : 'Làm mờ';
		const row = document.createElement('div');
		row.className = 'mask-row' + (sel ? ' mask-row-selected' : '');
		row.innerHTML =
			'<label style="display:flex;align-items:center;gap:4px;cursor:pointer;" title="Bật/tắt vùng che">' +
			'<input type="checkbox" ' + (m.enabled ? 'checked' : '') + ' onclick="videoComposeToggleMask(\'' + videoEsc(m.id) + '\')"></label>' +
			'<span class="mask-name">' + videoEsc(m.label || m.id) + '</span>' +
			'<span style="font-size:11px;color:var(--text-muted);">' + modeLabel + '</span>' +
			'<button class="btn-sm" onclick="videoComposeSelectMask(\'' + videoEsc(m.id) + '\')">Chọn</button>' +
			'<button class="btn-sm" onclick="videoComposeDupMask(\'' + videoEsc(m.id) + '\')">Nhân đôi</button>' +
			'<button class="btn-sm" style="color:var(--error);" onclick="videoComposeDeleteMask(\'' + videoEsc(m.id) + '\')">Xóa</button>';
		list.appendChild(row);
	}
	videoComposeMaskSyncForm();
	videoComposeSyncOverlay();
}

function videoComposeToggleMask(id) {
	const m = COMPOSE_MASKS.find(x => x.id === id);
	if (!m) return;
	m.enabled = !m.enabled;
	videoComposeSyncOverlay();
}

/* Overlay helpers */
function videoPctToPx(v, size) {
	return (v / 100) * size;
}

function videoComposeOverlayRect() {
	const wrap = document.querySelector('.video-preview-wrap');
	if (!wrap) return null;
	const video = document.getElementById('videoPreview');
	const vr = wrap.getBoundingClientRect();
	if (!video || !video.videoWidth) return { left: 0, top: 0, width: vr.width, height: vr.height, wrap };
	return { left: 0, top: 0, width: vr.width, height: vr.height, wrap };
}

function videoComposeSyncOverlay() {
	const layer = document.getElementById('composerMaskLayer');
	const rect = videoComposeOverlayRect();
	syncSubtitleOverlay();
	if (!rect) return;
	layer.innerHTML = '';
	const on = document.getElementById('composeMaskEnabled').checked;
	if (!on) return;
	for (const m of COMPOSE_MASKS) {
		const d = document.createElement('div');
		d.className = 'composer-mask' + (m.id === COMPOSE_MASK_SELECTED ? ' mask-selected' : '') + (m.enabled ? '' : ' mask-disabled');
		const left = videoPctToPx(m.x, rect.width);
		const top = videoPctToPx(m.y, rect.height);
		const width = videoPctToPx(m.w, rect.width);
		const height = videoPctToPx(m.h, rect.height);
		d.style.left = left + 'px';
		d.style.top = top + 'px';
		d.style.width = width + 'px';
		d.style.height = height + 'px';
		if (m.mode === 'box') {
			d.style.background = 'rgba(0,0,0,' + m.boxOpacity + ')';
		} else {
			d.style.background = 'rgba(0,0,0,0.18)';
		}
		d.style.display = m.enabled ? '' : 'none';
		d.dataset.maskId = m.id;
		const label = document.createElement('div');
		label.className = 'mask-label';
		label.textContent = m.label || m.id;
		d.appendChild(label);
		if (m.enabled) {
			for (const hc of ['h-nw', 'h-ne', 'h-sw', 'h-se']) {
				const h = document.createElement('div');
				h.className = 'mask-handle ' + hc;
				h.dataset.handle = hc;
				d.appendChild(h);
			}
		}
		d.addEventListener('mousedown', e => onMaskMouseDown(e, m.id));
		layer.appendChild(d);
	}
}

function syncSubtitleOverlay() {
	const layer = document.getElementById('composerSubtitleOverlay');
	const rect = videoComposeOverlayRect();
	if (!rect) { return; }
	if (!COMPOSE_SUBS_ENABLED) { layer.style.display = 'none'; return; }
	layer.style.display = 'block';
	const fs = videoComposeFontSize();
	layer.style.fontSize = fs + 'px';
	const pos = document.getElementById('composeSubsPosition').value;
	if (pos === 'top') layer.style.top = '4%';
	else if (pos === 'middle') layer.style.top = '45%';
	else layer.style.top = 'auto';
	if (pos === 'bottom') layer.style.bottom = videoPctToPx((document.getElementById('composeSubsMarginBottom').value / 100) * 18, rect.height) + 'px';
	else { layer.style.bottom = 'auto'; }
	const boxOn = document.getElementById('composeSubsBox').checked;
	const boxO = document.getElementById('composeSubsBoxOpacity').value / 100;
	if (boxOn) {
		layer.style.background = 'rgba(0,0,0,' + boxO + ')';
		layer.style.padding = '2px 8px';
		layer.style.borderRadius = '3px';
	} else {
		layer.style.background = 'transparent';
		layer.style.padding = '0';
	}
	layer.style.color = document.getElementById('composeSubsColor').value;
	layer.style.textShadow = videoComposeTextShadow();
	const outlineW = parseInt(document.getElementById('composeSubsOutlineWidth').value, 10) || 0;
	const labelVal = document.getElementById('composeSubsFontSizeVal');
	if (labelVal) labelVal.textContent = fs;
}

function videoComposeTextShadow() {
	return '2px 2px 0 #000, -2px 2px 0 #000, 2px -2px 0 #000, -2px -2px 0 #000';
}

function videoComposeFontSize() {
	return parseInt(document.getElementById('composeSubsFontSize').value, 10) || 48;
}

function videoComposeRenderSubtitle() {
	const layer = document.getElementById('composerSubtitleOverlay');
	const v = videoCurrentSegmentText();
	if (!COMPOSE_SUBS_ENABLED) { layer.innerHTML = ''; layer.style.display = 'none'; return; }
	layer.innerHTML = videoEsc(v) + '<div class="sub-resize" title="Kéo để phóng to/thu nhỏ"></div>';
	syncSubtitleOverlay();
}

function videoCurrentSegmentText() {
	const overlay = document.getElementById('composerSubtitleOverlay');
	void overlay;
	const video = document.getElementById('videoPreview');
	if (!video) return '';
	if (typeof VIDEO_VI_SEGMENTS === 'undefined' || !VIDEO_VI_SEGMENTS) return 'Xin chào';
	const t = video.currentTime || 0;
	const seg = VIDEO_VI_SEGMENTS.find(s => t >= (s.start || 0) && t <= (s.end || (s.start || 0) + 5));
	if (!seg || !seg.text) return '';
	return seg.text;
}

function videoComposeSetSubtitleText(t) {
	const layer = document.getElementById('composerSubtitleOverlay');
	if (!COMPOSE_SUBS_ENABLED) return;
	const v = t !== undefined && t !== null ? t : videoCurrentSegmentText();
	layer.innerHTML = videoEsc(v) + '<div class="sub-resize" title="Kéo để phóng to/thu nhỏ"></div>';
}

function videoComposeFontRangeChanged() {
	const val = parseInt(document.getElementById('composeSubsFontSize').value, 10) || 48;
	document.getElementById('composeSubsFontSizeVal').textContent = val;
	videoComposeRenderSubtitle();
	videoComposeRefreshButtons();
}

function videoComposeSubsFormChanged() {
	videoComposeRenderSubtitle();
}

function composeSetFontPreset(fs) {
	document.getElementById('composeSubsFontSize').value = fs;
	document.getElementById('composeSubsFontSizeVal').textContent = fs;
	videoComposeRenderSubtitle();
}

/* Drag/resize handlers */
function onMaskMouseDown(e, maskId) {
	e.preventDefault();
	const rect = videoComposeOverlayRect();
	if (!rect) return;
	videoComposeSelectMask(maskId);
	if (e.target && e.target.classList && e.target.classList.contains('mask-handle')) {
		startMaskResize(e, maskId, e.target.dataset.handle);
		return;
	}
	const m = COMPOSE_MASKS.find(x => x.id === maskId);
	if (!m) return;
	const startX = e.clientX, startY = e.clientY;
	const orig = { x: m.x, y: m.y };
	const move = ev => {
		const rect2 = videoComposeOverlayRect();
		if (!rect2) return;
		const dx = (ev.clientX - startX) / rect2.width * 100;
		const dy = (ev.clientY - startY) / rect2.height * 100;
		m.x = Math.max(0, Math.min(100 - m.w, orig.x + dx));
		m.y = Math.max(0, Math.min(100 - m.h, orig.y + dy));
		videoComposeSyncOverlay();
	};
	const up = () => {
		document.removeEventListener('mousemove', move);
		document.removeEventListener('mouseup', up);
		videoComposeMaskSyncForm();
	};
	document.addEventListener('mousemove', move);
	document.addEventListener('mouseup', up);
}

function startMaskResize(e, maskId, handle) {
	const rect = videoComposeOverlayRect();
	if (!rect) return;
	const m = COMPOSE_MASKS.find(x => x.id === maskId);
	if (!m) return;
	const startX = e.clientX, startY = e.clientY;
	const orig = { x: m.x, y: m.y, w: m.w, h: m.h };
	const resize = ev => {
		const rect2 = videoComposeOverlayRect();
		if (!rect2) return;
		const dx = (ev.clientX - startX) / rect2.width * 100;
		const dy = (ev.clientY - startY) / rect2.height * 100;
		let nx = orig.x, ny = orig.y, nw = orig.w, nh = orig.h;
		if (handle.includes('e')) nw = orig.w + dx;
		if (handle.includes('s')) nh = orig.h + dy;
		if (handle.includes('w')) { nw = orig.w - dx; nx = orig.x + dx; }
		if (handle.includes('n')) { nh = orig.h - dy; ny = orig.y + dy; }
		if (nw >= 1) { m.w = Math.min(nw, 100 - nx); m.x = Math.max(0, Math.min(nx, 100 - m.w)); }
		if (nh >= 1) { m.h = Math.min(nh, 100 - ny); m.y = Math.max(0, Math.min(ny, 100 - m.h)); }
		videoComposeSyncOverlay();
	};
	const up = () => {
		document.removeEventListener('mousemove', resize);
		document.removeEventListener('mouseup', up);
		videoComposeMaskSyncForm();
	};
	document.addEventListener('mousemove', resize);
	document.addEventListener('mouseup', up);
}

function videoComposeSubtitleMouseDown(e) {
	if (e.target && e.target.classList && e.target.classList.contains('sub-resize')) {
		videoComposeSubtitleResize(e);
		return;
	}
	e.preventDefault();
	const rect = videoComposeOverlayRect();
	if (!rect) return;
	const startY = e.clientY;
	const pos = document.getElementById('composeSubsPosition');
	const margin = document.getElementById('composeSubsMarginBottom');
	const startMargin = parseInt(margin.value, 10) || 60;
	const move = ev => {
		const rect2 = videoComposeOverlayRect();
		if (!rect2) return;
		if (pos.value === 'bottom') {
			const dy = (startY - ev.clientY) / rect2.height * 100;
			margin.value = Math.max(0, Math.min(200, Math.round(startMargin + dy * 20)));
		} else {
			// drag up/down switches position roughly
			const dy = (ev.clientY - startY) / rect2.height * 100;
			if (dy < -25) { pos.value = 'top'; }
			else if (dy > 25) { pos.value = 'bottom'; }
			else { pos.value = 'middle'; }
		}
		videoComposeRenderSubtitle();
	};
	const up = () => {
		document.removeEventListener('mousemove', move);
		document.removeEventListener('mouseup', up);
	};
	document.addEventListener('mousemove', move);
	document.addEventListener('mouseup', up);
}

function videoComposeSubtitleResize(e) {
	e.preventDefault();
	const startY = e.clientY;
	const startFs = videoComposeFontSize();
	const rect = videoComposeOverlayRect();
	if (!rect) return;
	const move = ev => {
		const dy = (ev.clientY - startY) / (rect ? rect.height : 320);
		let fs = startFs + dy * 300;
		fs = Math.max(20, Math.min(96, Math.round(fs)));
		document.getElementById('composeSubsFontSize').value = fs;
		document.getElementById('composeSubsFontSizeVal').textContent = fs;
		videoComposeRenderSubtitle();
	};
	const up = () => {
		document.removeEventListener('mousemove', move);
		document.removeEventListener('mouseup', up);
		videoComposeRefreshButtons();
	};
	document.addEventListener('mousemove', move);
	document.addEventListener('mouseup', up);
}

function videoComposeSyncOverlayHandlers() {
	const layer = document.getElementById('composerSubtitleOverlay');
	if (layer) {
		layer.removeEventListener('mousedown', videoComposeSubtitleMouseDown);
		layer.addEventListener('mousedown', videoComposeSubtitleMouseDown);
	}
}

function videoComposeResetOverlay() {
	document.getElementById('composerMaskLayer').innerHTML = '';
	document.getElementById('composerSubtitleOverlay').innerHTML = '';
	document.getElementById('composerSubtitleOverlay').style.display = 'none';
}

function videoComposeSyncEvents() {
	videoComposeSyncOverlayHandlers();
	if (COMPOSE_SUBS_ENABLED) videoComposeRenderSubtitle();
}

function workspaceTab(name, btn) {
	document.querySelectorAll('.ws-panel').forEach(p => p.classList.remove('active'));
	document.querySelectorAll('.ws-tab').forEach(t => t.classList.remove('active'));
	const panel = document.getElementById('ws-' + name);
	if (panel) panel.classList.add('active');
	if (btn) btn.classList.add('active');
	if (name === 'segments') workspaceHighlightCurrentSegment();
}

function workspaceSourceMediaURL(id, srcType, refId) {
	if (srcType === 'dub') {
		return `${API_BASE}/api/videos/${encodeURIComponent(id)}/dubs/${encodeURIComponent(refId)}/media`;
	}
	if (srcType === 'export') {
		return `${API_BASE}/api/videos/${encodeURIComponent(id)}/exports/${encodeURIComponent(refId)}/media`;
	}
	return `${API_BASE}/api/videos/${encodeURIComponent(id)}/media`;
}

function workspaceRenderSourceSelect() {
	const sel = document.getElementById('workspaceSourceSelect');
	if (!sel) return;
	const prev = sel.value;
	sel.innerHTML = '';
	const add = (val, label) => {
		const o = document.createElement('option');
		o.value = val;
		o.textContent = label;
		sel.appendChild(o);
	};
	add('original', 'Video gốc');
	(WORKSPACE_DUBS || []).forEach(d => add('dub:' + d.dub_id, 'Dub: ' + d.dub_id));
	(WORKSPACE_EXPORTS || []).forEach(e => add('export:' + e.export_id, 'Export: ' + e.export_id));
	// default: newest dub when it exists and nothing chosen yet
	if (!CURRENT_DUB_ID && !CURRENT_EXPORT_ID && CURRENT_WORKING_SOURCE === 'original' && (WORKSPACE_DUBS || []).length) {
		CURRENT_WORKING_SOURCE = 'dub';
		CURRENT_DUB_ID = WORKSPACE_DUBS[0].dub_id;
		VIDEO_DUB_SELECTED = CURRENT_DUB_ID;
	}
	if ((CURRENT_WORKING_SOURCE === 'dub' && CURRENT_DUB_ID && sel.value !== 'dub:' + CURRENT_DUB_ID)) {
		sel.value = 'dub:' + CURRENT_DUB_ID;
	} else if ((CURRENT_WORKING_SOURCE === 'export' && CURRENT_EXPORT_ID && sel.value !== 'export:' + CURRENT_EXPORT_ID)) {
		sel.value = 'export:' + CURRENT_EXPORT_ID;
	} else if (prev && Array.from(sel.options).some(o => o.value === prev)) {
		sel.value = prev;
	} else {
		sel.value = 'original';
	}
	if (!sel.value) sel.value = 'original';
	const key = CURRENT_WORKING_SOURCE + ':' + (CURRENT_DUB_ID || CURRENT_EXPORT_ID || 'original');
	const preview = document.getElementById('videoPreview');
	if (preview && VIDEO_CURRENT_ID && CURRENT_WORKING_SOURCE !== 'original' && preview.getAttribute('data-ws-source') !== key) {
		preview.src = workspaceSourceMediaURL(VIDEO_CURRENT_ID, CURRENT_WORKING_SOURCE, CURRENT_DUB_ID || CURRENT_EXPORT_ID);
		preview.setAttribute('data-ws-source', key);
		preview.load();
		preview.play().catch(() => { });
	}
	workspaceSyncSourceUI();
}

function workspaceSourceChange() {
	const sel = document.getElementById('workspaceSourceSelect');
	const val = sel.value || 'original';
	if (val === 'original') {
		CURRENT_WORKING_SOURCE = 'original';
		CURRENT_DUB_ID = null;
		CURRENT_EXPORT_ID = null;
	} else if (val.startsWith('dub:')) {
		CURRENT_WORKING_SOURCE = 'dub';
		CURRENT_DUB_ID = val.slice(4);
		CURRENT_EXPORT_ID = null;
		VIDEO_DUB_SELECTED = CURRENT_DUB_ID;
	} else if (val.startsWith('export:')) {
		CURRENT_WORKING_SOURCE = 'export';
		CURRENT_EXPORT_ID = val.slice(7);
		CURRENT_DUB_ID = null;
	}
	const preview = document.getElementById('videoPreview');
	if (preview && VIDEO_CURRENT_ID) {
		preview.src = workspaceSourceMediaURL(VIDEO_CURRENT_ID, CURRENT_WORKING_SOURCE, CURRENT_DUB_ID || CURRENT_EXPORT_ID);
		preview.setAttribute('data-ws-source', CURRENT_WORKING_SOURCE + ':' + (CURRENT_DUB_ID || CURRENT_EXPORT_ID || 'original'));
		preview.currentTime = 0;
		preview.load();
	}
	workspaceSyncSourceUI();
	workspaceUpdateExportSummary();
	workspaceHighlightCurrentSegment();
}

function workspaceSyncSourceUI() {
	const badge = document.getElementById('workspaceSourceBadge');
	const metaEl = document.getElementById('workspacePreviewMeta');
	let label = 'Gốc';
	let cls = '';
	if (CURRENT_WORKING_SOURCE === 'dub') {
		label = 'Dub · ' + (CURRENT_DUB_ID || '');
		cls = 'src-dub';
	} else if (CURRENT_WORKING_SOURCE === 'export') {
		label = 'Export · ' + (CURRENT_EXPORT_ID || '');
		cls = 'src-export';
	}
	if (badge) { badge.textContent = label; badge.className = 'ws-source-badge ' + cls; }
	if (metaEl) {
		metaEl.textContent = (CURRENT_WORKING_SOURCE === 'dub' || CURRENT_WORKING_SOURCE === 'export')
			? `Đang xem bản ${CURRENT_WORKING_SOURCE} (${CURRENT_DUB_ID || CURRENT_EXPORT_ID || ''})`
			: 'Đang xem video gốc (index.mp4)';
	}
}

async function workspaceLoadExports(id) {
	const listEl = document.getElementById('videoExportsList');
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/exports`);
		if (!res.ok) throw new Error(`Lỗi ${res.status}`);
		const items = await res.json();
		WORKSPACE_EXPORTS = items || [];
		if (!items || !items.length) {
			listEl.innerHTML = '<div class="no-chunks">Chưa có bản export nào — mở tab "Chỉnh video" và bấm Xuất.</div>';
		} else {
			listEl.innerHTML = items.map(e => {
				const sel = CURRENT_WORKING_SOURCE === 'export' && CURRENT_EXPORT_ID === e.export_id;
				return '<div style="border:1px solid ' + (sel ? 'var(--success)' : 'var(--card-border)') + ';border-radius:var(--radius-sm);padding:8px 10px;background:var(--card-bg);">'
					+ '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">'
					+ '<div style="font-size:12px;font-weight:600;color:var(--text-secondary);">' + videoEsc(e.export_id) + '</div>'
					+ '<div style="font-size:11px;color:var(--text-muted);">' + videoFmtTime(e.created_at)
					+ (e.width ? ` · ${e.width}x${e.height}` : '')
					+ (e.subtitle_enabled ? ' · có phụ đề' : '')
					+ (e.enabled_masks ? ` · ${e.enabled_masks} vùng che` : '') + '</div></div>'
					+ '<div style="display:flex;gap:6px;margin-top:6px;flex-wrap:wrap;">'
					+ `<button class="btn-sm" onclick="workspaceSelectExport('${videoEsc(id)}','${videoEsc(e.export_id)}')">Xem</button>`
					+ `<a class="btn-sm" href="${API_BASE}/api/videos/${encodeURIComponent(id)}/exports/${encodeURIComponent(e.export_id)}/media" download style="text-decoration:none;">Download MP4</a>`
					+ '</div></div>';
			}).join('');
		}
		workspaceRenderSourceSelect();
	} catch (e) {
		listEl.innerHTML = '<div class="no-chunks">Không tải được danh sách export: ' + videoEsc(e.message) + '</div>';
	}
}

function workspaceSelectExport(id, exportId) {
	CURRENT_WORKING_SOURCE = 'export';
	CURRENT_EXPORT_ID = exportId;
	CURRENT_DUB_ID = null;
	VIDEO_DUB_SELECTED = null;
	const preview = document.getElementById('videoPreview');
	if (preview) {
		preview.src = `${API_BASE}/api/videos/${encodeURIComponent(id)}/exports/${encodeURIComponent(exportId)}/media`;
		preview.load();
		preview.play().catch(() => { });
	}
	workspaceSyncSourceUI();
	workspaceRenderSourceSelect();
	workspaceLoadExports(id);
	workspaceUpdateExportSummary();
	workspaceHighlightCurrentSegment();
}

function workspaceSegmentFmt(t) {
	if (t === undefined || t === null || isNaN(t)) return '0';
	t = Math.max(0, t);
	return t.toFixed(2);
}

async function workspaceLoadSegments(id) {
	const statusEl = document.getElementById('segmentEditorStatus');
	const listEl = document.getElementById('segmentList');
	statusEl.textContent = 'Đang tải…';
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/segments`);
		if (!res.ok) throw new Error(`Lỗi ${res.status}`);
		const data = await res.json();
		WORKSPACE_ORIG_SEGMENTS = data.original_segments || [];
		WORKSPACE_VI_SEGMENTS = data.vi_segments || [];
		if (!WORKSPACE_ORIG_SEGMENTS.length && !WORKSPACE_VI_SEGMENTS.length) {
			listEl.innerHTML = '<div class="no-chunks">Chưa có transcript — bấm Transcribe ở Video Library.</div>';
			statusEl.textContent = '';
			return;
		}
		workspaceRenderSegments();
		statusEl.textContent = WORKSPACE_ORIG_SEGMENTS.length + ' đoạn';
	} catch (e) {
		listEl.innerHTML = '<div class="no-chunks">Không tải được đoạn: ' + videoEsc(e.message) + '</div>';
		statusEl.textContent = '';
	}
}

function workspaceRenderSegments() {
	const listEl = document.getElementById('segmentList');
	const count = Math.max(WORKSPACE_ORIG_SEGMENTS.length, WORKSPACE_VI_SEGMENTS.length);
	if (!count) { listEl.innerHTML = '<div class="no-chunks">Chưa có đoạn nào.</div>'; return; }
	listEl.innerHTML = '';
	for (let i = 0; i < count; i++) {
		const orig = WORKSPACE_ORIG_SEGMENTS[i] || {};
		const vi = WORKSPACE_VI_SEGMENTS[i] || {};
		const start = orig.start !== undefined ? orig.start : (vi.start !== undefined ? vi.start : 0);
		const end = orig.end !== undefined ? orig.end : (vi.end !== undefined ? vi.end : start + 5);
		const card = document.createElement('div');
		card.className = 'segment-card' + (i === WORKSPACE_CURRENT_SEGMENT ? ' segment-current' : '');
		card.dataset.segIndex = i;
		card.innerHTML =
			'<div class="segment-card-head">'
			+ '<span class="segment-no">#' + (i + 1) + '</span>'
			+ '<span class="segment-time-inputs">'
			+ '<input type="number" step="0.01" min="0" class="seg-start" value="' + workspaceSegmentFmt(start) + '" title="Bắt đầu (giây)">'
			+ '–'
			+ '<input type="number" step="0.01" min="0" class="seg-end" value="' + workspaceSegmentFmt(end) + '" title="Kết thúc (giây)">'
			+ 's</span>'
			+ '<span class="segment-badge">gốc</span>'
			+ '<span class="segment-badge" style="' + (WORKSPACE_VI_SEGMENTS.length ? '' : 'display:none;') + '">vi</span>'
			+ '</div>'
			+ '<div class="segment-textareas">'
			+ '<textarea class="seg-orig" placeholder="Transcript gốc" data-vi="0">' + videoEsc(orig.text || '') + '</textarea>'
			+ '<textarea class="seg-vi" placeholder="Bản dịch tiếng Việt" data-vi="1"' + (WORKSPACE_VI_SEGMENTS.length ? '' : ' disabled') + '>' + videoEsc(vi.text || '') + '</textarea>'
			+ '</div>'
			+ '<div class="segment-card-actions">'
			+ '<button class="btn-sm" onclick="workspacePlaySegment(' + i + ')">Phát đoạn</button>'
			+ '<button class="btn-sm" onclick="workspaceSaveSegment(' + i + ')">Lưu đoạn</button>'
			+ '<button class="btn-sm" onclick="workspaceSegmentToTTS(' + i + ')">Đưa vào TTS</button>'
			+ '<span class="seg-save-status" style="font-size:11px;color:var(--success);margin-left:auto;"></span>'
			+ '</div>';
		listEl.appendChild(card);
	}
}

function workspacePlaySegment(idx) {
	const video = document.getElementById('videoPreview');
	if (!video || !VIDEO_CURRENT_ID) return;
	const orig = WORKSPACE_ORIG_SEGMENTS[idx];
	const vi = WORKSPACE_VI_SEGMENTS[idx];
	const start = orig && orig.start !== undefined ? orig.start : (vi ? vi.start : 0);
	video.currentTime = start || 0;
	video.play().catch(() => { });
	workspaceHighlightCurrentSegment(true);
}

function workspaceHighlightCurrentSegment(force) {
	if (!VIDEO_CURRENT_ID) return;
	const video = document.getElementById('videoPreview');
	let t = video ? video.currentTime : 0;
	let idx = -1;
	const base = (WORKSPACE_VI_SEGMENTS && WORKSPACE_VI_SEGMENTS.length) ? WORKSPACE_VI_SEGMENTS : WORKSPACE_ORIG_SEGMENTS;
	if (base && base.length) {
		idx = base.findIndex(s => t >= (s.start || 0) && t <= (s.end || (s.start || 0) + 5));
	}
	if (force) idx = WORKSPACE_CURRENT_SEGMENT;
	if (idx === WORKSPACE_CURRENT_SEGMENT && !force) return;
	WORKSPACE_CURRENT_SEGMENT = idx;
	document.querySelectorAll('.segment-card').forEach(c => {
		c.classList.toggle('segment-current', parseInt(c.dataset.segIndex, 10) === idx);
	});
	if (idx >= 0) {
		const card = document.querySelector('.segment-card[data-seg-index="' + idx + '"]');
		if (card) card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
	}
}

async function workspaceSaveSegment(idx) {
	const card = document.querySelector('.segment-card[data-seg-index="' + idx + '"]');
	if (!card || !VIDEO_CURRENT_ID) return;
	const startInput = card.querySelector('.seg-start');
	const endInput = card.querySelector('.seg-end');
	const origTa = card.querySelector('.seg-orig');
	const viTa = card.querySelector('.seg-vi');
	const statusEl = card.querySelector('.seg-save-status');
	const body = {
		start: parseFloat(startInput.value),
		end: parseFloat(endInput.value),
		original_text: origTa.value,
		vi_text: WORKSPACE_VI_SEGMENTS.length ? viTa.value : null,
	};
	try {
		const res = await fetch(`${API_BASE}/api/videos/${encodeURIComponent(VIDEO_CURRENT_ID)}/segments/${idx}`, {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body),
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) { statusEl.textContent = 'Lỗi: ' + (data.detail || res.status); statusEl.style.color = 'var(--error)'; return; }
		if (WORKSPACE_ORIG_SEGMENTS[idx]) { WORKSPACE_ORIG_SEGMENTS[idx].start = body.start; WORKSPACE_ORIG_SEGMENTS[idx].end = body.end; WORKSPACE_ORIG_SEGMENTS[idx].text = body.original_text; }
		if (WORKSPACE_VI_SEGMENTS[idx]) { WORKSPACE_VI_SEGMENTS[idx].start = body.start; WORKSPACE_VI_SEGMENTS[idx].end = body.end; WORKSPACE_VI_SEGMENTS[idx].text = body.vi_text; }
		statusEl.textContent = 'Đã lưu ✓';
		statusEl.style.color = 'var(--success)';
		setTimeout(() => { statusEl.textContent = ''; }, 2500);
		window.VIDEO_VI_SEGMENTS = WORKSPACE_VI_SEGMENTS.length ? WORKSPACE_VI_SEGMENTS : window.VIDEO_VI_SEGMENTS;
		window.VIDEO_TRANSCRIPT_SEGMENTS = WORKSPACE_ORIG_SEGMENTS.length ? WORKSPACE_ORIG_SEGMENTS : window.VIDEO_TRANSCRIPT_SEGMENTS;
		if (COMPOSE_SUBS_ENABLED) videoComposeRenderSubtitle();
	} catch (e) {
		statusEl.textContent = 'Lỗi kết nối';
		statusEl.style.color = 'var(--error)';
	}
}

function workspaceSegmentToTTS(idx) {
	const seg = WORKSPACE_VI_SEGMENTS[idx] || WORKSPACE_ORIG_SEGMENTS[idx];
	if (!seg) return;
	if (typeof textInput !== 'undefined' && textInput) {
		textInput.value = seg.text || '';
		if (typeof updateCharCount === 'function') updateCharCount();
	}
	const dz = document.getElementById('dropZone');
	if (dz) dz.classList.remove('empty');
}

function workspaceUpdateExportSummary() {
	const srcEl = document.getElementById('exportSourceLabel');
	const maskEl = document.getElementById('exportMaskLabel');
	const subsEl = document.getElementById('exportSubsLabel');
	if (srcEl) {
		const label = CURRENT_WORKING_SOURCE === 'dub' ? ('Bản dub: ' + CURRENT_DUB_ID)
			: CURRENT_WORKING_SOURCE === 'export' ? ('Bản export: ' + CURRENT_EXPORT_ID)
			: 'Video gốc';
		srcEl.textContent = label;
	}
	const maskOn = document.getElementById('composeMaskEnabled');
	const subsOn = document.getElementById('composeSubsEnabled');
	if (maskEl) maskEl.textContent = (maskOn && maskOn.checked) ? 'Bật (' + COMPOSE_MASKS.filter(m => m.enabled).length + ' vùng)' : 'Tắt';
	if (subsEl) subsEl.textContent = (subsOn && subsOn.checked) ? 'Bật' : 'Tắt';
}

function workspaceFinalExport() {
	if (VIDEO_CURRENT_ID) videoComposeExport(VIDEO_CURRENT_ID);
}

function workspaceOpenFinalFolder() {
	if (VIDEO_CURRENT_ID) videoOpenFolder(VIDEO_CURRENT_ID);
}

function workspaceClear() {
	WORKSPACE_DUBS = [];
	WORKSPACE_EXPORTS = [];
	WORKSPACE_ORIG_SEGMENTS = [];
	WORKSPACE_VI_SEGMENTS = [];
	WORKSPACE_CURRENT_SEGMENT = -1;
	CURRENT_WORKING_SOURCE = 'original';
	CURRENT_DUB_ID = null;
	CURRENT_EXPORT_ID = null;
	VIDEO_DUB_SELECTED = null;
}

function videoEditorInit() {
	if (window.__videoEditorBound) return;
	window.__videoEditorBound = true;
	const video = document.getElementById('videoPreview');
	if (video) {
		video.addEventListener('timeupdate', () => {
			if (COMPOSE_SUBS_ENABLED || document.getElementById('composeMaskEnabled').checked) {
				videoComposeRenderSubtitle();
			}
			workspaceHighlightCurrentSegment();
		});
		video.addEventListener('loadedmetadata', () => {
			videoComposeSyncOverlay();
			videoComposeRenderSubtitle();
		});
		video.addEventListener('play', () => videoComposeSyncOverlay());
		window.addEventListener('resize', () => videoComposeSyncOverlay());
	}
	const subOverlay = document.getElementById('composerSubtitleOverlay');
	if (subOverlay) {
		subOverlay.addEventListener('mousedown', videoComposeSubtitleMouseDown);
	}
	document.getElementById('videoComposeBtn').addEventListener('click', () => { if (VIDEO_CURRENT_ID) videoComposeExport(VIDEO_CURRENT_ID); });
	document.getElementById('videoComposePreviewBtn').addEventListener('click', () => { if (VIDEO_CURRENT_ID) { videoComposeRenderSubtitle(); videoComposeSyncOverlay(); } });
	document.getElementById('videoOpenComposeFolderBtn').addEventListener('click', () => videoComposeResetOverlay());
	videoComposeRefreshButtons();
}

function videoComposeForm() {
	const enabledMasks = document.getElementById('composeMaskEnabled').checked
		? COMPOSE_MASKS.map(m => ({
			id: m.id, x: m.x, y: m.y, w: m.w, h: m.h, mode: m.mode,
			blur: m.blur, box_color: '#000000', box_opacity: m.boxOpacity, enabled: m.enabled,
		}))
		: [];
	return {
		source: CURRENT_WORKING_SOURCE === 'dub' ? 'dub' : 'original',
		dub_id: CURRENT_WORKING_SOURCE === 'dub' ? CURRENT_DUB_ID : null,
		aspect_ratio: 'original', width: 1080, height: 1920, fit_mode: 'contain', background_color: '#000000',
		old_subtitle_masks_enabled: document.getElementById('composeMaskEnabled').checked,
		old_subtitle_masks: enabledMasks,
		subtitle_enabled: document.getElementById('composeSubsEnabled').checked,
		subtitle_position: document.getElementById('composeSubsPosition').value,
		subtitle_font_size: videoComposeFontSize(),
		subtitle_color: document.getElementById('composeSubsColor').value,
		subtitle_outline_color: document.getElementById('composeSubsOutlineColor').value,
		subtitle_outline_width: parseInt(document.getElementById('composeSubsOutlineWidth').value, 10) || 2,
		subtitle_box: document.getElementById('composeSubsBox').checked,
		subtitle_box_color: '#000000',
		subtitle_box_opacity: (parseInt(document.getElementById('composeSubsBoxOpacity').value, 10) || 0) / 100,
		subtitle_margin_bottom: parseInt(document.getElementById('composeSubsMarginBottom').value, 10) || 60,
		subtitle_max_chars: 22,
	};
}

function videoComposeCurrentSegments() {
	const video = document.getElementById('videoPreview');
	if (!video) return [];
	const t = video.currentTime || 0;
	if (typeof VIDEO_VI_SEGMENTS !== 'undefined' && VIDEO_VI_SEGMENTS) {
		const seg = VIDEO_VI_SEGMENTS.find(s => t >= (s.start || 0) && t <= (s.end || (s.start || 0) + 5));
		if (seg) return [{ start: seg.start || t, end: (seg.end || seg.start + 5), text: seg.text || '' }];
	}
	if (typeof VIDEO_TRANSCRIPT_SEGMENTS !== 'undefined' && VIDEO_TRANSCRIPT_SEGMENTS) {
		const seg = VIDEO_TRANSCRIPT_SEGMENTS.find(s => t >= (s.start || 0) && t <= (s.end || (s.start || 0) + 5));
		if (seg) return [{ start: seg.start || t, end: (seg.end || seg.start + 5), text: seg.text || '' }];
	}
	return [];
}

function videoComposeExportSegments() {
	const all = (typeof VIDEO_VI_SEGMENTS !== 'undefined' && VIDEO_VI_SEGMENTS && VIDEO_VI_SEGMENTS.length)
		? VIDEO_VI_SEGMENTS
		: ((typeof VIDEO_TRANSCRIPT_SEGMENTS !== 'undefined' && VIDEO_TRANSCRIPT_SEGMENTS) || []);
	return all
		.filter(s => s && s.text && String(s.text).trim())
		.map(s => ({ start: s.start || 0, end: (s.end || (s.start || 0) + 5), text: String(s.text) }));
}

function videoComposeExport(id) {
	const body = videoComposeForm();
	body.subtitle_segments = videoComposeExportSegments();
	videoComposeStatus('Đang gửi yêu cầu export...');
	fetch(`${API_BASE}/api/videos/${encodeURIComponent(id)}/compose`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	})
		.then(r => r.json().then(d => ({ ok: r.ok, d })))
		.then(({ ok, d }) => {
			if (!ok) { videoComposeStatus(d.detail || ('Lỗi ' + d.status)); return; }
			videoComposePoll(d.job_id);
		})
		.catch(e => videoComposeStatus('Không kết nối được server: ' + e.message));
}

function videoComposePoll(jobId) {
	const tick = () => {
		fetch(`${API_BASE}/api/videos/compose/${jobId}`)
			.then(r => r.json())
			.then(j => {
				videoComposeStatus(j.progress + '% ' + (j.message || j.status));
				if (j.status === 'done') { videoComposeStatus('Xong! ' + (j.message || '')); return; }
				if (j.status === 'failed') { videoComposeStatus('Thất bại: ' + (j.error || j.message || '')); return; }
				setTimeout(tick, 1000);
			})
			.catch(() => setTimeout(tick, 1500));
	};
	tick();
}

function videoComposeStatus(msg) {
	const el = document.getElementById('videoComposeStatus');
	if (el) el.textContent = msg || '';
}

document.getElementById('videoDownloadBtn').addEventListener('click', videoStartDownload);
document.getElementById('videoUrlInput').addEventListener('input', videoDetectUrl);
document.getElementById('videoDetailCloseBtn').addEventListener('click', () => {
	document.getElementById('videoDetailCard').classList.add('hidden');
	document.getElementById('videoPreview').pause();
});
document.getElementById('videoUseTranscriptBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoSendToTTS(VIDEO_CURRENT_ID, 'original');
});
document.getElementById('videoUseViTranscriptBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoSendToTTS(VIDEO_CURRENT_ID, 'vi');
});
document.getElementById('videoTranslateBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoTranslate(VIDEO_CURRENT_ID);
});
document.getElementById('videoOpenFolderBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoOpenFolder(VIDEO_CURRENT_ID);
});
document.getElementById('videoDubBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoDub(VIDEO_CURRENT_ID);
});
document.getElementById('videoPreviewDubBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoPreviewDub(VIDEO_CURRENT_ID);
});
document.getElementById('videoOpenDubFolderBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoOpenDubFolder(VIDEO_CURRENT_ID);
});
document.getElementById('videoFinalExportBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) workspaceFinalExport();
});
document.getElementById('videoOpenFinalFolderBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) workspaceOpenFinalFolder();
});

onDubSlider('dubOriginalVolume', 'dubOriginalVolumeVal');
onDubSlider('dubDubVolume', 'dubDubVolumeVal');
onDubSlider('dubMaxSpeed', 'dubMaxSpeedVal');
onDubModeChange();
document.getElementById('videoDeleteBtn').addEventListener('click', () => {
	if (VIDEO_CURRENT_ID) videoDelete(VIDEO_CURRENT_ID);
});
document.getElementById('videoUrlInput').addEventListener('keydown', e => {
	if (e.key === 'Enter') videoStartDownload();
});
videoLoadLibrary();
