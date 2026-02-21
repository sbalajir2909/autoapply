// ATS Resume Builder - AI Powered Frontend v3.0 with Streaming

// Global state
let currentLatex = '';
let originalLatex = '';
let currentValidation = null;
let checkedKeywords = new Set();

document.addEventListener('DOMContentLoaded', () => {
    checkAIStatus();
    setupTabs();
    setupFileUploads();
    setupButtons();
    setupOutputTabs();
    setupModal();
    setupKeywordChecklist();
});

async function checkAIStatus() {
    const statusEl = document.getElementById('ai-status');
    try {
        const response = await fetch('/api/health');
        const data = await response.json();

        if (data.ai_enabled) {
            statusEl.innerHTML = '<span class="dot active"></span><span>AI Ready</span>';
            statusEl.classList.add('active');
        } else {
            statusEl.innerHTML = '<span class="dot warning"></span><span>AI Key Not Set</span>';
            statusEl.classList.add('warning');
        }
    } catch (error) {
        statusEl.innerHTML = '<span class="dot error"></span><span>Connection Error</span>';
        statusEl.classList.add('error');
    }
}

function setupTabs() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const panel = e.target.closest('.panel');
            const tabId = e.target.dataset.tab;

            panel.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');

            panel.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
        });
    });
}

function setupFileUploads() {
    document.getElementById('resume-file').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            document.getElementById('resume-filename').textContent = file.name;
            const reader = new FileReader();
            reader.onload = (event) => {
                document.getElementById('resume-input').value = event.target.result;
            };
            reader.readAsText(file);
        }
    });

    document.getElementById('job-file').addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (file) {
            document.getElementById('job-filename').textContent = file.name;
            const reader = new FileReader();
            reader.onload = (event) => {
                document.getElementById('job-input').value = event.target.result;
            };
            reader.readAsText(file);
        }
    });
}

function setupButtons() {
    document.getElementById('analyze-btn').addEventListener('click', analyzeJob);
    document.getElementById('tailor-btn').addEventListener('click', tailorResumeStream);
    document.getElementById('import-url-btn').addEventListener('click', importFromURL);

    // Copy buttons
    document.getElementById('copy-latex').addEventListener('click', () => {
        copyToClipboard(document.getElementById('latex-code').textContent, 'LaTeX');
    });

    document.getElementById('copy-report').addEventListener('click', () => {
        copyToClipboard(window.matchReport || '', 'Report');
    });

    // Download buttons
    document.getElementById('download-latex').addEventListener('click', () => {
        downloadFile('tailored_resume.tex', document.getElementById('latex-code').textContent);
    });

    document.getElementById('download-report').addEventListener('click', () => {
        downloadFile('match_report.md', window.matchReport || '');
    });

    // Export buttons
    document.getElementById('export-pdf').addEventListener('click', exportPDF);
    document.getElementById('export-txt').addEventListener('click', exportTXT);
}

function setupOutputTabs() {
    document.querySelectorAll('.output-tab').forEach(tab => {
        tab.addEventListener('click', (e) => {
            const outputId = e.target.dataset.output;

            document.querySelectorAll('.output-tab').forEach(t => t.classList.remove('active'));
            e.target.classList.add('active');

            document.querySelectorAll('.output-content').forEach(c => c.classList.remove('active'));
            document.getElementById(`${outputId}-output`).classList.add('active');
        });
    });
}

function setupModal() {
    const modal = document.getElementById('edit-modal');
    const closeBtn = document.getElementById('modal-close');
    const cancelBtn = document.getElementById('edit-cancel');
    const saveBtn = document.getElementById('edit-save');
    const newTextArea = document.getElementById('edit-new-text');

    closeBtn.addEventListener('click', () => modal.classList.add('hidden'));
    cancelBtn.addEventListener('click', () => modal.classList.add('hidden'));

    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.classList.add('hidden');
    });

    newTextArea.addEventListener('input', updateEditStats);
    saveBtn.addEventListener('click', saveBulletEdit);
}

function setupKeywordChecklist() {
    document.addEventListener('click', (e) => {
        if (e.target.classList.contains('keyword-tag') &&
            e.target.closest('.checklist')) {
            e.target.classList.toggle('checked');
            const keyword = e.target.textContent;
            if (checkedKeywords.has(keyword)) {
                checkedKeywords.delete(keyword);
            } else {
                checkedKeywords.add(keyword);
            }
        }
    });
}

async function importFromURL() {
    const urlInput = document.getElementById('job-url-input');
    const url = urlInput.value.trim();

    if (!url) {
        showError('Please enter a URL');
        return;
    }

    showLoading(true, 'Importing job description from URL...', 0);
    hideError();

    try {
        const response = await fetch('/api/import-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to import URL');
        }

        const data = await response.json();
        document.getElementById('job-input').value = data.job_description;

        const pasteBtn = document.querySelector('[data-tab="job-paste"]');
        pasteBtn.click();

    } catch (error) {
        showError(error.message);
    } finally {
        showLoading(false);
    }
}

async function analyzeJob() {
    const jobDescription = document.getElementById('job-input').value;

    if (!jobDescription.trim()) {
        showError('Please enter a job description first.');
        return;
    }

    showLoading(true, 'AI is analyzing the job description...', 0);
    hideError();

    try {
        const formData = new FormData();
        formData.append('job_description', jobDescription);

        const response = await fetch('/api/analyze-job', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to analyze job description');
        }

        const data = await response.json();
        displayJobAnalysis(data);

    } catch (error) {
        showError(error.message);
    } finally {
        showLoading(false);
    }
}

async function tailorResumeStream() {
    const resumeContent = document.getElementById('resume-input').value;
    const jobDescription = document.getElementById('job-input').value;
    const maxExperiences = parseInt(document.getElementById('max-experiences').value) || 4;
    const maxBullets = parseInt(document.getElementById('max-bullets').value) || 4;

    if (!resumeContent.trim()) {
        showError('Please enter your resume content.');
        return;
    }

    if (!jobDescription.trim()) {
        showError('Please enter a job description.');
        return;
    }

    showLoading(true, 'Starting AI tailoring...', 0);
    hideError();

    try {
        const response = await fetch('/api/tailor/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                resume_content: resumeContent,
                job_description: jobDescription,
                max_experiences: maxExperiences,
                max_bullets: maxBullets
            })
        });

        if (!response.ok) {
            throw new Error('Failed to start tailoring');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        handleStreamUpdate(data);
                    } catch (e) {
                        console.error('Failed to parse SSE data:', e);
                    }
                }
            }
        }

    } catch (error) {
        showError(error.message);
        showLoading(false);
    }
}

function handleStreamUpdate(data) {
    if (data.step === 'error') {
        showError(data.message);
        showLoading(false);
        return;
    }

    if (data.step === 'result') {
        // Final result received
        currentLatex = data.tailored_resume;
        originalLatex = data.original_resume;
        currentValidation = data.validation;

        displayResults(data);

        if (data.job_analysis) {
            displayJobAnalysisFromResult(data.job_analysis);
        }

        checkCompilation(currentLatex);
        showLoading(false);
        return;
    }

    // Progress update
    const progress = data.progress || 0;
    const message = data.message || 'Processing...';

    showLoading(true, message, progress);

    // Show additional data if available
    if (data.data) {
        if (data.data.keywords_count) {
            showLoading(true, `${message} (${data.data.keywords_count} keywords found)`, progress);
        }
        if (data.data.coverage !== undefined) {
            showLoading(true, `${message} (${data.data.coverage}% keyword coverage)`, progress);
        }
    }
}

function displayJobAnalysis(data) {
    const container = document.getElementById('job-analysis');
    container.classList.remove('hidden');

    const jobTitle = document.getElementById('job-title');
    if (data.role_title) {
        jobTitle.textContent = data.role_title;
        jobTitle.classList.remove('hidden');
    }

    const requiredSkills = document.getElementById('required-skills');
    requiredSkills.innerHTML = (data.required_skills || [])
        .map(s => `<span class="keyword-tag required">${s}</span>`)
        .join('') || '<span class="empty-state">None specified</span>';

    const preferredSkills = document.getElementById('preferred-skills');
    preferredSkills.innerHTML = (data.preferred_skills || [])
        .map(s => `<span class="keyword-tag preferred">${s}</span>`)
        .join('') || '<span class="empty-state">None specified</span>';

    const techSkills = document.getElementById('tech-skills');
    techSkills.innerHTML = (data.key_technologies || [])
        .map(s => `<span class="keyword-tag tech">${s}</span>`)
        .join('') || '<span class="empty-state">None specified</span>';

    const actionVerbs = document.getElementById('action-verbs');
    actionVerbs.innerHTML = (data.action_verbs || [])
        .map(v => `<span class="keyword-tag verb">${v}</span>`)
        .join('') || '<span class="empty-state">None extracted</span>';

    const responsibilitiesSection = document.getElementById('responsibilities-section');
    const responsibilitiesList = document.getElementById('responsibilities-list');
    if (data.key_responsibilities && data.key_responsibilities.length > 0) {
        responsibilitiesSection.classList.remove('hidden');
        responsibilitiesList.innerHTML = data.key_responsibilities
            .slice(0, 5)
            .map(r => `<li>${r}</li>`)
            .join('');
    } else {
        responsibilitiesSection.classList.add('hidden');
    }
}

function displayJobAnalysisFromResult(analysis) {
    const container = document.getElementById('job-analysis');
    container.classList.remove('hidden');

    const jobTitle = document.getElementById('job-title');
    if (analysis.role_title) {
        jobTitle.textContent = analysis.role_title;
    }

    const requiredSkills = document.getElementById('required-skills');
    requiredSkills.innerHTML = (analysis.required_skills || [])
        .map(s => `<span class="keyword-tag required">${s}</span>`)
        .join('') || '<span class="empty-state">None specified</span>';

    const preferredSkills = document.getElementById('preferred-skills');
    preferredSkills.innerHTML = (analysis.preferred_skills || [])
        .map(s => `<span class="keyword-tag preferred">${s}</span>`)
        .join('') || '<span class="empty-state">None specified</span>';

    const techSkills = document.getElementById('tech-skills');
    techSkills.innerHTML = (analysis.key_technologies || [])
        .map(s => `<span class="keyword-tag tech">${s}</span>`)
        .join('') || '<span class="empty-state">None specified</span>';

    const actionVerbs = document.getElementById('action-verbs');
    actionVerbs.innerHTML = (analysis.action_verbs || [])
        .map(v => `<span class="keyword-tag verb">${v}</span>`)
        .join('') || '<span class="empty-state">None extracted</span>';

    const responsibilitiesSection = document.getElementById('responsibilities-section');
    const responsibilitiesList = document.getElementById('responsibilities-list');
    if (analysis.key_responsibilities && analysis.key_responsibilities.length > 0) {
        responsibilitiesSection.classList.remove('hidden');
        responsibilitiesList.innerHTML = analysis.key_responsibilities
            .slice(0, 5)
            .map(r => `<li>${r}</li>`)
            .join('');
    }
}

function displayResults(data) {
    const container = document.getElementById('results');
    container.classList.remove('hidden');

    const passesBadge = document.getElementById('passes-badge');
    passesBadge.textContent = `${data.passes_completed || 1} pass${data.passes_completed > 1 ? 'es' : ''} completed`;

    displayValidationStatus(data.validation);
    displayVerbTracker(data.validation.verb_counts);

    const score = data.keyword_coverage;
    const scoreProgress = document.getElementById('score-progress');
    scoreProgress.style.strokeDasharray = `${score}, 100`;

    if (score >= 80) {
        scoreProgress.style.stroke = '#22c55e';
    } else if (score >= 60) {
        scoreProgress.style.stroke = '#f59e0b';
    } else {
        scoreProgress.style.stroke = '#ef4444';
    }

    document.getElementById('score-value').textContent = `${score}%`;

    const matchedKeywords = document.getElementById('matched-keywords');
    document.getElementById('matched-count').textContent = `(${data.matched_keywords.length})`;
    matchedKeywords.innerHTML = data.matched_keywords
        .map(k => `<span class="keyword-tag">${k}</span>`)
        .join('') || '<span class="empty-state">None matched</span>';

    const missingKeywords = document.getElementById('missing-keywords');
    document.getElementById('missing-count').textContent = `(${data.missing_keywords.length})`;
    missingKeywords.innerHTML = data.missing_keywords
        .map(k => `<span class="keyword-tag">${k}</span>`)
        .join('') || '<span class="empty-state">All keywords matched!</span>';

    displayBulletSuggestions(data.bullet_suggestions);

    document.getElementById('latex-code').textContent = data.tailored_resume;

    displayDiffView(data.diff_data);
    displayBulletAnalysis(data.validation.bullet_analyses);

    window.matchReport = generateMatchReport(data);
    document.getElementById('report-content').innerHTML = markdownToHtml(window.matchReport);

    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function displayValidationStatus(validation) {
    const badge = document.getElementById('validation-badge');
    const totalBullets = document.getElementById('total-bullets');
    const wrongLength = document.getElementById('bullets-wrong-length');
    const noQuant = document.getElementById('bullets-no-quant');
    const repeatedVerbs = document.getElementById('repeated-verbs-count');

    if (validation.is_valid) {
        badge.textContent = 'All Rules Passed';
        badge.className = 'validation-badge valid';
    } else {
        const issues = validation.bullets_wrong_length + validation.bullets_no_quantification + validation.repeated_verbs.length;
        badge.textContent = `${issues} Issues Found`;
        badge.className = 'validation-badge invalid';
    }

    totalBullets.textContent = validation.total_bullets;
    totalBullets.className = 'validation-value';

    wrongLength.textContent = validation.bullets_wrong_length;
    wrongLength.className = `validation-value ${validation.bullets_wrong_length === 0 ? 'good' : 'bad'}`;

    noQuant.textContent = validation.bullets_no_quantification;
    noQuant.className = `validation-value ${validation.bullets_no_quantification === 0 ? 'good' : 'bad'}`;

    repeatedVerbs.textContent = validation.repeated_verbs.length;
    repeatedVerbs.className = `validation-value ${validation.repeated_verbs.length === 0 ? 'good' : 'bad'}`;
}

function displayVerbTracker(verbCounts) {
    const container = document.getElementById('verb-tracker-content');

    if (!verbCounts || Object.keys(verbCounts).length === 0) {
        container.innerHTML = '<p class="empty-state">No verbs detected</p>';
        return;
    }

    const sorted = Object.entries(verbCounts).sort((a, b) => b[1] - a[1]);

    container.innerHTML = sorted.map(([verb, count]) => {
        let className = 'verb-item';
        if (count > 2) className += ' overused';
        else if (count === 2) className += ' warning';

        return `
            <div class="${className}">
                <span class="verb-name">${verb}</span>
                <span class="verb-count">${count}</span>
            </div>
        `;
    }).join('');
}

function displayBulletSuggestions(suggestions) {
    const container = document.getElementById('bullet-suggestions');
    const list = document.getElementById('suggestions-list');

    if (!suggestions || suggestions.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    list.innerHTML = suggestions.map(s => `
        <div class="suggestion-item">
            <span class="suggestion-keyword">${s.keyword}</span>
            <p class="suggestion-text">${s.suggestion}</p>
        </div>
    `).join('');
}

function displayDiffView(diffData) {
    const container = document.getElementById('diff-content');

    if (!diffData || (!diffData.added && !diffData.removed && !diffData.modified)) {
        container.innerHTML = '<p class="empty-state">No changes to display</p>';
        return;
    }

    let html = '';

    if (diffData.removed && diffData.removed.length > 0) {
        html += `
            <div class="diff-section">
                <div class="diff-section-title">Removed Bullets</div>
                ${diffData.removed.map(line => `<div class="diff-line removed">${escapeHtml(line)}</div>`).join('')}
            </div>
        `;
    }

    if (diffData.added && diffData.added.length > 0) {
        html += `
            <div class="diff-section">
                <div class="diff-section-title">Added/Modified Bullets</div>
                ${diffData.added.map(line => `<div class="diff-line added">${escapeHtml(line)}</div>`).join('')}
            </div>
        `;
    }

    container.innerHTML = html || '<p class="empty-state">No significant changes</p>';
}

function displayBulletAnalysis(bullets) {
    const container = document.getElementById('bullets-analysis');
    const validStat = document.getElementById('valid-bullets-stat');
    const invalidStat = document.getElementById('invalid-bullets-stat');

    if (!bullets || bullets.length === 0) {
        container.innerHTML = '<p class="empty-state">No bullets to analyze</p>';
        return;
    }

    const validCount = bullets.filter(b => b.is_valid_length && b.has_quantification).length;
    const invalidCount = bullets.length - validCount;

    validStat.textContent = `${validCount} valid`;
    invalidStat.textContent = `${invalidCount} issues`;

    container.innerHTML = bullets.map((bullet, index) => {
        const isValid = bullet.is_valid_length && bullet.has_quantification;

        return `
            <div class="bullet-card ${isValid ? 'valid' : 'invalid'}">
                <div class="bullet-text">${escapeHtml(bullet.text)}</div>
                <div class="bullet-meta">
                    <div class="bullet-meta-item">
                        Words: <span class="value ${bullet.is_valid_length ? 'good' : 'bad'}">${bullet.word_count}</span>
                        <span class="hint">(24-28)</span>
                    </div>
                    <div class="bullet-meta-item">
                        Verb: <span class="value">${bullet.action_verb || 'None'}</span>
                    </div>
                    <div class="bullet-meta-item">
                        Quant: <span class="value ${bullet.has_quantification ? 'good' : 'bad'}">${bullet.has_quantification ? 'Yes' : 'No'}</span>
                    </div>
                    ${bullet.keywords_found && bullet.keywords_found.length > 0 ? `
                        <div class="bullet-meta-item">
                            Keywords: <span class="value good">${bullet.keywords_found.length}</span>
                        </div>
                    ` : ''}
                    <button class="bullet-edit-btn" onclick="openEditModal(${index})">Edit</button>
                </div>
            </div>
        `;
    }).join('');
}

function openEditModal(bulletIndex) {
    const bullet = currentValidation.bullet_analyses[bulletIndex];
    if (!bullet) return;

    const modal = document.getElementById('edit-modal');
    const originalText = document.getElementById('edit-original-text');
    const newText = document.getElementById('edit-new-text');

    originalText.textContent = bullet.text;
    newText.value = bullet.text;
    newText.dataset.bulletIndex = bulletIndex;

    updateEditStats();
    modal.classList.remove('hidden');
}

function updateEditStats() {
    const newText = document.getElementById('edit-new-text');
    const wordCount = document.getElementById('edit-word-count');
    const quantStatus = document.getElementById('edit-quant-status');

    const text = newText.value.trim();
    const words = text.split(/\s+/).filter(w => w.length > 0).length;
    const hasQuant = /\d+%?|\$[\d,]+|[\d,]+\+?/.test(text);

    wordCount.textContent = `${words} words`;
    wordCount.className = (words >= 24 && words <= 28) ? 'valid' : 'invalid';

    quantStatus.textContent = hasQuant ? 'Has quantification' : 'No quantification';
    quantStatus.className = hasQuant ? 'valid' : 'invalid';
}

async function saveBulletEdit() {
    const newText = document.getElementById('edit-new-text');
    const bulletIndex = parseInt(newText.dataset.bulletIndex);
    const originalBullet = currentValidation.bullet_analyses[bulletIndex].text;
    const newBullet = newText.value.trim();

    if (newBullet === originalBullet) {
        document.getElementById('edit-modal').classList.add('hidden');
        return;
    }

    showLoading(true, 'Saving bullet edit...', 0);

    try {
        const response = await fetch('/api/edit-bullet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                latex_content: currentLatex,
                old_bullet: originalBullet,
                new_bullet: newBullet
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to save edit');
        }

        const data = await response.json();

        currentLatex = data.updated_latex;
        document.getElementById('latex-code').textContent = currentLatex;

        if (data.bullet_analysis) {
            currentValidation.bullet_analyses[bulletIndex] = {
                ...currentValidation.bullet_analyses[bulletIndex],
                text: newBullet,
                word_count: data.bullet_analysis.word_count,
                has_quantification: data.bullet_analysis.has_quantification,
                is_valid_length: data.bullet_analysis.is_valid_length,
                action_verb: data.bullet_analysis.action_verb
            };
            displayBulletAnalysis(currentValidation.bullet_analyses);
        }

        document.getElementById('edit-modal').classList.add('hidden');

    } catch (error) {
        showError(error.message);
    } finally {
        showLoading(false);
    }
}

async function checkCompilation(latex) {
    const compileStatus = document.getElementById('compile-status');
    const compileIcon = document.getElementById('compile-icon');
    const compileText = document.getElementById('compile-text');
    const compileDetails = document.getElementById('compile-details');

    compileStatus.classList.remove('hidden', 'success', 'error');
    compileIcon.textContent = '⏳';
    compileText.textContent = 'Checking LaTeX compilation...';
    compileDetails.textContent = '';

    try {
        const formData = new FormData();
        formData.append('latex_content', latex);

        const response = await fetch('/api/compile-check', {
            method: 'POST',
            body: formData
        });

        const data = await response.json();

        if (data.success) {
            compileStatus.classList.add('success');
            compileIcon.textContent = '✓';
            compileText.textContent = 'LaTeX compiles successfully';
            compileDetails.textContent = data.is_one_page
                ? 'Output: 1 page (perfect!)'
                : `Output: ${data.pages} pages (consider reducing content)`;
        } else {
            compileStatus.classList.add('error');
            compileIcon.textContent = '✗';
            compileText.textContent = 'LaTeX compilation failed';
            compileDetails.textContent = data.errors || 'Unknown error';
        }
    } catch (error) {
        compileStatus.classList.add('error');
        compileIcon.textContent = '✗';
        compileText.textContent = 'Could not check compilation';
        compileDetails.textContent = error.message;
    }
}

async function exportPDF() {
    const latex = document.getElementById('latex-code').textContent;
    if (!latex) {
        showError('No LaTeX content to export');
        return;
    }

    showLoading(true, 'Generating PDF (this may take a moment)...', 0);

    try {
        const formData = new FormData();
        formData.append('latex_content', latex);

        const response = await fetch('/api/export/pdf', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to generate PDF');
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'tailored_resume.pdf';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

    } catch (error) {
        // Offer .tex download as fallback
        const shouldDownloadTex = confirm(
            `PDF generation failed: ${error.message}\n\n` +
            `Would you like to download the .tex file instead?\n` +
            `You can compile it using Overleaf.com or a local LaTeX installation.`
        );
        if (shouldDownloadTex) {
            downloadFile('tailored_resume.tex', latex);
        }
    } finally {
        showLoading(false);
    }
}

async function exportTXT() {
    const latex = document.getElementById('latex-code').textContent;
    if (!latex) {
        showError('No content to export');
        return;
    }

    showLoading(true, 'Converting to plain text...', 0);

    try {
        const formData = new FormData();
        formData.append('latex_content', latex);

        const response = await fetch('/api/export/txt', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to convert to text');
        }

        const data = await response.json();
        downloadFile(data.filename, data.content);

    } catch (error) {
        showError(error.message);
    } finally {
        showLoading(false);
    }
}

function generateMatchReport(data) {
    let report = `# Resume Tailoring Report\n\n`;
    report += `## Keyword Coverage: ${data.keyword_coverage}%\n\n`;

    report += `### Matched Keywords (${data.matched_keywords.length})\n`;
    report += data.matched_keywords.map(k => `- ${k}`).join('\n') + '\n\n';

    report += `### Missing Keywords (${data.missing_keywords.length})\n`;
    report += data.missing_keywords.map(k => `- ${k}`).join('\n') + '\n\n';

    if (data.validation) {
        report += `## Validation Results\n\n`;
        report += `- Total Bullets: ${data.validation.total_bullets}\n`;
        report += `- Invalid Length: ${data.validation.bullets_wrong_length}\n`;
        report += `- Missing Quantification: ${data.validation.bullets_no_quantification}\n`;
        report += `- Repeated Verbs: ${data.validation.repeated_verbs.join(', ') || 'None'}\n\n`;
    }

    if (data.suggestions && data.suggestions.length > 0) {
        report += `## Suggestions\n\n`;
        report += data.suggestions.map(s => `- ${s}`).join('\n') + '\n';
    }

    return report;
}

function showLoading(show, text = 'Processing...', progress = 0) {
    const loading = document.getElementById('loading');
    const loadingText = document.getElementById('loading-text');
    const buttons = document.querySelectorAll('.btn');

    if (show) {
        let displayText = text;
        if (progress > 0) {
            displayText = `${text} (${progress}%)`;
        }
        loadingText.textContent = displayText;
        loading.classList.remove('hidden');
        buttons.forEach(btn => btn.disabled = true);
    } else {
        loading.classList.add('hidden');
        buttons.forEach(btn => btn.disabled = false);
    }
}

function showError(message) {
    const errorEl = document.getElementById('error-message');
    const errorText = document.getElementById('error-text');
    errorText.textContent = message;
    errorEl.classList.remove('hidden');
}

function hideError() {
    document.getElementById('error-message').classList.add('hidden');
}

function copyToClipboard(text, label) {
    navigator.clipboard.writeText(text).then(() => {
        const btn = label === 'LaTeX' ? document.getElementById('copy-latex') : document.getElementById('copy-report');
        const originalText = btn.textContent;
        btn.textContent = '✓ Copied!';
        setTimeout(() => {
            btn.textContent = originalText;
        }, 2000);
    }).catch(err => {
        console.error('Failed to copy:', err);
        showError('Failed to copy to clipboard');
    });
}

function downloadFile(filename, content) {
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function markdownToHtml(markdown) {
    return markdown
        .replace(/^### (.*$)/gim, '<h3>$1</h3>')
        .replace(/^## (.*$)/gim, '<h2>$1</h2>')
        .replace(/^# (.*$)/gim, '<h1>$1</h1>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\*([^*]+)\*/g, '<em>$1</em>')
        .replace(/_([^_]+)_/g, '<em>$1</em>')
        .replace(/^\- (.*$)/gim, '<li>$1</li>')
        .replace(/^---$/gim, '<hr>')
        .replace(/\n\n/g, '</p><p>')
        .replace(/\n/g, '<br>');
}
