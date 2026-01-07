/**
 * Pipeline Animation Engine
 * Creates immersive visual experience during audio generation
 */

class PipelineVisualizer {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext('2d');
        this.particles = [];
        this.currentStage = null;
        this.stageProgress = 0;
        this.animationId = null;
        this.isRunning = false;

        // Stage colors
        this.stageColors = {
            analyze: { primary: '#00d4ff', secondary: '#0891b2' },
            research: { primary: '#3b82f6', secondary: '#1d4ed8' },
            enhance: { primary: '#8b5cf6', secondary: '#7c3aed' },
            generate: { primary: '#22c55e', secondary: '#16a34a' },
            combine: { primary: '#f59e0b', secondary: '#d97706' }
        };

        // Stage titles
        this.stageTitles = {
            analyze: 'Analyzing Script',
            research: 'Researching Topics',
            enhance: 'Enhancing Dialogue',
            generate: 'Generating Audio',
            combine: 'Finalizing'
        };

        this.resize();
        window.addEventListener('resize', () => this.resize());
    }

    resize() {
        const dpr = window.devicePixelRatio || 1;
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * dpr;
        this.canvas.height = rect.height * dpr;
        this.canvas.style.width = rect.width + 'px';
        this.canvas.style.height = rect.height + 'px';
        this.ctx.scale(dpr, dpr);
        this.width = rect.width;
        this.height = rect.height;
        this.centerX = this.width / 2;
        this.centerY = this.height / 2;
    }

    start() {
        if (this.isRunning) return;
        this.isRunning = true;
        this.animate();
    }

    stop() {
        this.isRunning = false;
        if (this.animationId) {
            cancelAnimationFrame(this.animationId);
            this.animationId = null;
        }
    }

    setStage(stage, progress = 0) {
        const previousStage = this.currentStage;
        this.currentStage = stage;
        this.stageProgress = progress;

        // Spawn new particles for stage transition
        if (previousStage !== stage) {
            this.transitionToStage(stage);
        }

        // Update stage indicators in DOM
        this.updateStageIndicators(stage);
    }

    updateStageIndicators(activeStage) {
        const stages = document.querySelectorAll('.stage');
        const connectors = document.querySelectorAll('.stage-connector');
        const stageOrder = ['analyze', 'research', 'enhance', 'generate', 'combine'];
        const activeIndex = stageOrder.indexOf(activeStage);

        stages.forEach((el, i) => {
            const stageName = el.dataset.stage;
            const stageIndex = stageOrder.indexOf(stageName);

            el.classList.remove('active', 'completed', 'pending');

            if (stageIndex < activeIndex) {
                el.classList.add('completed');
            } else if (stageIndex === activeIndex) {
                el.classList.add('active');
            } else {
                el.classList.add('pending');
            }
        });

        connectors.forEach((el, i) => {
            el.classList.remove('active', 'completed');
            if (i < activeIndex) {
                el.classList.add('completed');
            } else if (i === activeIndex) {
                el.classList.add('active');
            }
        });

        // Update title
        const titleEl = document.getElementById('stage-title');
        if (titleEl && this.stageTitles[activeStage]) {
            titleEl.textContent = this.stageTitles[activeStage];
        }
    }

    transitionToStage(stage) {
        // Clear old particles
        this.particles = [];

        // Spawn stage-specific particles
        const count = 80;
        for (let i = 0; i < count; i++) {
            this.particles.push(this.createParticle(stage));
        }
    }

    createParticle(stage) {
        const colors = this.stageColors[stage] || this.stageColors.analyze;
        const angle = Math.random() * Math.PI * 2;
        const distance = Math.random() * Math.max(this.width, this.height) * 0.6;

        return {
            x: this.centerX + Math.cos(angle) * distance,
            y: this.centerY + Math.sin(angle) * distance,
            vx: (Math.random() - 0.5) * 2,
            vy: (Math.random() - 0.5) * 2,
            size: Math.random() * 4 + 1,
            color: Math.random() > 0.5 ? colors.primary : colors.secondary,
            alpha: Math.random() * 0.5 + 0.3,
            life: 1,
            decay: Math.random() * 0.002 + 0.001,
            stage: stage
        };
    }

    animate() {
        if (!this.isRunning) return;

        this.ctx.clearRect(0, 0, this.width, this.height);

        // Draw background based on stage
        this.drawBackground();

        // Draw perspective tunnel
        this.drawTunnel();

        // Update and draw particles
        this.updateParticles();
        this.drawParticles();

        // Draw stage-specific effects
        this.drawStageEffects();

        this.animationId = requestAnimationFrame(() => this.animate());
    }

    drawBackground() {
        const colors = this.stageColors[this.currentStage] || this.stageColors.analyze;

        // Radial gradient from center
        const gradient = this.ctx.createRadialGradient(
            this.centerX, this.centerY, 0,
            this.centerX, this.centerY, Math.max(this.width, this.height) * 0.7
        );
        gradient.addColorStop(0, this.hexToRgba(colors.primary, 0.15));
        gradient.addColorStop(0.5, this.hexToRgba(colors.secondary, 0.08));
        gradient.addColorStop(1, 'rgba(10, 10, 20, 0.95)');

        this.ctx.fillStyle = gradient;
        this.ctx.fillRect(0, 0, this.width, this.height);
    }

    drawTunnel() {
        const colors = this.stageColors[this.currentStage] || this.stageColors.analyze;
        const time = Date.now() * 0.001;

        // Draw perspective grid lines converging to center
        this.ctx.strokeStyle = this.hexToRgba(colors.primary, 0.1);
        this.ctx.lineWidth = 1;

        const rings = 8;
        for (let i = 1; i <= rings; i++) {
            const radius = (i / rings) * Math.max(this.width, this.height) * 0.5;
            const pulse = Math.sin(time * 2 + i * 0.5) * 10;

            this.ctx.beginPath();
            this.ctx.arc(this.centerX, this.centerY, radius + pulse, 0, Math.PI * 2);
            this.ctx.stroke();
        }

        // Radial lines
        const lines = 12;
        for (let i = 0; i < lines; i++) {
            const angle = (i / lines) * Math.PI * 2 + time * 0.1;
            const length = Math.max(this.width, this.height) * 0.6;

            this.ctx.beginPath();
            this.ctx.moveTo(this.centerX, this.centerY);
            this.ctx.lineTo(
                this.centerX + Math.cos(angle) * length,
                this.centerY + Math.sin(angle) * length
            );
            this.ctx.stroke();
        }
    }

    updateParticles() {
        const time = Date.now() * 0.001;

        this.particles.forEach((p, i) => {
            // Move towards center slowly
            const dx = this.centerX - p.x;
            const dy = this.centerY - p.y;
            const dist = Math.sqrt(dx * dx + dy * dy);

            if (dist > 20) {
                p.vx += dx / dist * 0.02;
                p.vy += dy / dist * 0.02;
            }

            // Add some orbital motion
            p.vx += Math.cos(time + i) * 0.01;
            p.vy += Math.sin(time + i) * 0.01;

            // Apply velocity
            p.x += p.vx;
            p.y += p.vy;

            // Damping
            p.vx *= 0.99;
            p.vy *= 0.99;

            // Decay
            p.life -= p.decay;

            // Respawn if dead
            if (p.life <= 0) {
                Object.assign(p, this.createParticle(this.currentStage));
            }
        });
    }

    drawParticles() {
        this.particles.forEach(p => {
            this.ctx.beginPath();
            this.ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
            this.ctx.fillStyle = this.hexToRgba(p.color, p.alpha * p.life);
            this.ctx.fill();

            // Glow effect
            this.ctx.shadowBlur = 10;
            this.ctx.shadowColor = p.color;
            this.ctx.fill();
            this.ctx.shadowBlur = 0;
        });
    }

    drawStageEffects() {
        const time = Date.now() * 0.001;
        const colors = this.stageColors[this.currentStage] || this.stageColors.analyze;

        switch (this.currentStage) {
            case 'analyze':
                this.drawScanLines(time, colors);
                break;
            case 'research':
                this.drawNetworkNodes(time, colors);
                break;
            case 'enhance':
                this.drawNeuralPulses(time, colors);
                break;
            case 'generate':
                this.drawSoundWaves(time, colors);
                break;
            case 'combine':
                this.drawConvergingRing(time, colors);
                break;
        }
    }

    drawScanLines(time, colors) {
        // Horizontal scan lines moving down
        const lineY = (time * 100) % this.height;

        this.ctx.strokeStyle = this.hexToRgba(colors.primary, 0.5);
        this.ctx.lineWidth = 2;
        this.ctx.beginPath();
        this.ctx.moveTo(0, lineY);
        this.ctx.lineTo(this.width, lineY);
        this.ctx.stroke();

        // Glow
        this.ctx.shadowBlur = 20;
        this.ctx.shadowColor = colors.primary;
        this.ctx.stroke();
        this.ctx.shadowBlur = 0;
    }

    drawNetworkNodes(time, colors) {
        // Floating connected nodes
        const nodes = 6;
        const nodePositions = [];

        for (let i = 0; i < nodes; i++) {
            const angle = (i / nodes) * Math.PI * 2 + time * 0.3;
            const radius = 80 + Math.sin(time + i) * 20;
            const x = this.centerX + Math.cos(angle) * radius;
            const y = this.centerY + Math.sin(angle) * radius;
            nodePositions.push({ x, y });

            // Draw node
            this.ctx.beginPath();
            this.ctx.arc(x, y, 6, 0, Math.PI * 2);
            this.ctx.fillStyle = colors.primary;
            this.ctx.fill();

            this.ctx.shadowBlur = 15;
            this.ctx.shadowColor = colors.primary;
            this.ctx.fill();
            this.ctx.shadowBlur = 0;
        }

        // Draw connections
        this.ctx.strokeStyle = this.hexToRgba(colors.primary, 0.3);
        this.ctx.lineWidth = 1;
        for (let i = 0; i < nodes; i++) {
            for (let j = i + 1; j < nodes; j++) {
                this.ctx.beginPath();
                this.ctx.moveTo(nodePositions[i].x, nodePositions[i].y);
                this.ctx.lineTo(nodePositions[j].x, nodePositions[j].y);
                this.ctx.stroke();
            }
        }
    }

    drawNeuralPulses(time, colors) {
        // Synaptic pulses radiating
        const pulses = 4;
        for (let i = 0; i < pulses; i++) {
            const phase = (time * 0.5 + i / pulses) % 1;
            const radius = phase * 150;
            const alpha = 1 - phase;

            this.ctx.beginPath();
            this.ctx.arc(this.centerX, this.centerY, radius, 0, Math.PI * 2);
            this.ctx.strokeStyle = this.hexToRgba(colors.primary, alpha * 0.5);
            this.ctx.lineWidth = 3;
            this.ctx.stroke();
        }
    }

    drawSoundWaves(time, colors) {
        // Audio waveform bars
        const bars = 24;
        const barWidth = 8;
        const maxHeight = 60;
        const spacing = 4;
        const totalWidth = bars * (barWidth + spacing);
        const startX = this.centerX - totalWidth / 2;

        for (let i = 0; i < bars; i++) {
            const height = (Math.sin(time * 3 + i * 0.5) * 0.5 + 0.5) * maxHeight + 10;
            const x = startX + i * (barWidth + spacing);
            const y = this.centerY - height / 2;

            // Use progress to light up bars from left to right
            const barProgress = i / bars;
            const isActive = barProgress <= this.stageProgress;
            const alpha = isActive ? 0.8 : 0.3;

            this.ctx.fillStyle = this.hexToRgba(colors.primary, alpha);
            this.ctx.fillRect(x, y, barWidth, height);

            if (isActive) {
                this.ctx.shadowBlur = 10;
                this.ctx.shadowColor = colors.primary;
                this.ctx.fillRect(x, y, barWidth, height);
                this.ctx.shadowBlur = 0;
            }
        }
    }

    drawConvergingRing(time, colors) {
        // Completion ring filling up
        const progress = this.stageProgress;
        const radius = 70;

        // Background ring
        this.ctx.beginPath();
        this.ctx.arc(this.centerX, this.centerY, radius, 0, Math.PI * 2);
        this.ctx.strokeStyle = this.hexToRgba(colors.secondary, 0.3);
        this.ctx.lineWidth = 8;
        this.ctx.stroke();

        // Progress ring
        this.ctx.beginPath();
        this.ctx.arc(
            this.centerX, this.centerY, radius,
            -Math.PI / 2,
            -Math.PI / 2 + (Math.PI * 2 * progress)
        );
        this.ctx.strokeStyle = colors.primary;
        this.ctx.lineWidth = 8;
        this.ctx.lineCap = 'round';
        this.ctx.stroke();

        // Glow
        this.ctx.shadowBlur = 20;
        this.ctx.shadowColor = colors.primary;
        this.ctx.stroke();
        this.ctx.shadowBlur = 0;

        // Sparkles at end
        if (progress > 0) {
            const endAngle = -Math.PI / 2 + (Math.PI * 2 * progress);
            const sparkleX = this.centerX + Math.cos(endAngle) * radius;
            const sparkleY = this.centerY + Math.sin(endAngle) * radius;

            this.ctx.beginPath();
            this.ctx.arc(sparkleX, sparkleY, 5 + Math.sin(time * 5) * 2, 0, Math.PI * 2);
            this.ctx.fillStyle = '#fff';
            this.ctx.fill();
        }
    }

    hexToRgba(hex, alpha) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }
}

// Global instance
let pipelineViz = null;

function initPipeline() {
    const canvas = document.getElementById('pipeline-canvas');
    if (canvas) {
        pipelineViz = new PipelineVisualizer('pipeline-canvas');
    }
}

function startPipeline(stage = 'analyze') {
    if (pipelineViz) {
        pipelineViz.setStage(stage);
        pipelineViz.start();
    }
}

function updatePipelineStage(stage, progress = 0) {
    if (pipelineViz) {
        pipelineViz.setStage(stage, progress);
    }
}

function stopPipeline() {
    if (pipelineViz) {
        pipelineViz.stop();
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', initPipeline);
