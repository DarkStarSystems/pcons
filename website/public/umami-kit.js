/**
 * UmamiTracker - A comprehensive utility for enhanced Umami Analytics tracking
 * Handles clicks, scroll depth, time on page, element visibility, and more
 *
 * Vendored from https://github.com/rhelmer/umami-kit (unmodified).
 */
class UmamiTracker {
    constructor(options = {}) {
        this.options = {
            // Scroll tracking options
            scrollDepthThresholds: [25, 50, 75, 90],
            scrollDebounceMs: 100,

            // Time tracking options
            heartbeatInterval: 30000, // 30 seconds
            idleTimeout: 60000, // 1 minute

            // Click tracking options
            autoTrackClicks: true,
            autoTrackAllClicks: false, // Track ALL clicks, not just data-umami-track elements
            clickSelector: '[data-umami-track]',

            // Visibility tracking options
            visibilityThreshold: 0.5, // 50% visible
            visibilitySelector: '[data-umami-visible]',

            // General options
            debug: false,
            ...options
        };

        this.state = {
            scrollDepthTracked: [],
            startTime: Date.now(),
            lastActivity: Date.now(),
            heartbeatTimer: null,
            isIdle: false,
            visibleElements: new Set()
        };

        this.init();
    }

    init() {
        this.waitForUmami(() => {
            this.log('UmamiTracker initialized');
            this.setupScrollTracking();
            this.setupTimeTracking();
            this.setupClickTracking();
            this.setupVisibilityTracking();
            this.setupIdleTracking();
            this.setupPageExitTracking();
        });
    }

    waitForUmami(callback, attempts = 0, maxAttempts = 50) {
        if (window.umami && typeof window.umami.track === 'function') {
            callback();
        } else if (attempts < maxAttempts) {
            setTimeout(() => this.waitForUmami(callback, attempts + 1, maxAttempts), 100);
        } else {
            console.warn('UmamiTracker: Umami not found after waiting');
        }
    }

    log(...args) {
        if (this.options.debug) {
            console.log('[UmamiTracker]', ...args);
        }
    }

    track(event, data = {}) {
        if (window.umami && typeof window.umami.track === 'function') {
            window.umami.track(event, data);
            this.log('Event tracked:', event, data);
        } else {
            this.log('Umami not available, event not tracked:', event, data);
        }
    }

    // Scroll Depth Tracking
    setupScrollTracking() {
        let scrollTimeout;

        const handleScroll = () => {
            clearTimeout(scrollTimeout);
            scrollTimeout = setTimeout(() => {
                this.checkScrollDepth();
            }, this.options.scrollDebounceMs);
        };

        window.addEventListener('scroll', handleScroll, { passive: true });
        this.log('Scroll tracking enabled');
    }

    checkScrollDepth() {
        const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
        const scrollPercent = Math.round((window.scrollY / scrollHeight) * 100);

        this.options.scrollDepthThresholds.forEach(threshold => {
            if (scrollPercent >= threshold && !this.state.scrollDepthTracked.includes(threshold)) {
                this.track('scroll-depth', {
                    depth: threshold,
                    percentage: `${threshold}%`,
                    pixels: window.scrollY
                });
                this.state.scrollDepthTracked.push(threshold);
                this.log(`Scroll depth tracked: ${threshold}%`);
            }
        });
    }

    // Time Tracking with Idle Detection
    setupTimeTracking() {
        this.startHeartbeat();
        this.log('Time tracking enabled');
    }

    startHeartbeat() {
        this.state.heartbeatTimer = setInterval(() => {
            if (!this.state.isIdle) {
                const timeSpent = Math.round((Date.now() - this.state.startTime) / 1000);
                this.track('time-on-page', {
                    seconds: timeSpent,
                    minutes: Math.round(timeSpent / 60)
                });
            }
        }, this.options.heartbeatInterval);
    }

    setupIdleTracking() {
        const resetIdleTimer = () => {
            this.state.lastActivity = Date.now();
            if (this.state.isIdle) {
                this.state.isIdle = false;
                this.track('user-active', {
                    idleDuration: Math.round((Date.now() - this.state.lastActivity) / 1000)
                });
            }
        };

        const checkIdle = () => {
            const timeSinceActivity = Date.now() - this.state.lastActivity;
            if (timeSinceActivity > this.options.idleTimeout && !this.state.isIdle) {
                this.state.isIdle = true;
                this.track('user-idle', {
                    timeBeforeIdle: Math.round(timeSinceActivity / 1000)
                });
            }
        };

        // Track user activity
        ['mousedown', 'mousemove', 'keypress', 'scroll', 'touchstart', 'click'].forEach(event => {
            document.addEventListener(event, resetIdleTimer, { passive: true });
        });

        // Check for idle state every 30 seconds
        setInterval(checkIdle, 30000);
        this.log('Idle tracking enabled');
    }

    // Click Tracking
    setupClickTracking() {
        if (!this.options.autoTrackClicks) return;

        // Track ALL clicks if autoTrackAllClicks is enabled
        if (this.options.autoTrackAllClicks) {
            document.addEventListener('click', (e) => {
                const element = e.target.closest('button, a, input[type="submit"], [role="button"]');
                if (element) {
                    const eventData = {
                        ...this.getElementData(element),
                        clickType: 'auto-tracked'
                    };
                    this.track('click', eventData);
                }
            });
            this.log('All clicks tracking enabled');
        }

        // Track elements with data-umami-track attribute
        document.addEventListener('click', (e) => {
            const element = e.target.closest(this.options.clickSelector);
            if (element) {
                const eventName = element.dataset.umamiTrack || 'click';
                const eventData = this.getElementData(element);
                this.track(eventName, eventData);
            }
        });

        // Track all external links
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a[href]');
            if (link && this.isExternalLink(link.href)) {
                this.track('external-link-click', {
                    url: link.href,
                    text: link.textContent.trim().substring(0, 50)
                });
            }
        });

        this.log('Click tracking enabled');
    }

    getElementData(element) {
        const data = {};

        // Extract custom data attributes
        Object.keys(element.dataset).forEach(key => {
            if (key.startsWith('umamiData')) {
                const dataKey = key.replace('umamiData', '').toLowerCase();
                data[dataKey] = element.dataset[key];
            }
        });

        // Add common element properties
        if (element.tagName) data.element = element.tagName.toLowerCase();
        if (element.id) data.id = element.id;
        if (element.className) data.classes = element.className;
        if (element.textContent) data.text = element.textContent.trim().substring(0, 50);
        if (element.href) data.href = element.href;

        return data;
    }

    isExternalLink(url) {
        try {
            const link = new URL(url, window.location.href);
            return link.hostname !== window.location.hostname;
        } catch {
            return false;
        }
    }

    // Element Visibility Tracking
    setupVisibilityTracking() {
        if (!window.IntersectionObserver) {
            this.log('IntersectionObserver not supported, visibility tracking disabled');
            return;
        }

        const observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const element = entry.target;
                const elementId = element.id || element.className || 'unnamed-element';

                if (entry.isIntersecting && !this.state.visibleElements.has(elementId)) {
                    this.state.visibleElements.add(elementId);
                    const eventName = element.dataset.umamiVisible || 'element-visible';
                    const eventData = {
                        element: elementId,
                        intersectionRatio: Math.round(entry.intersectionRatio * 100),
                        ...this.getElementData(element)
                    };
                    this.track(eventName, eventData);
                }
            });
        }, {
            threshold: this.options.visibilityThreshold
        });

        // Observe elements with visibility tracking
        document.querySelectorAll(this.options.visibilitySelector).forEach(el => {
            observer.observe(el);
        });

        this.log('Visibility tracking enabled');
    }

    // Page Exit Tracking
    setupPageExitTracking() {
        const trackPageExit = () => {
            const totalTime = Math.round((Date.now() - this.state.startTime) / 1000);
            const maxScroll = Math.max(...this.state.scrollDepthTracked, 0);

            this.track('page-exit', {
                totalTimeSeconds: totalTime,
                maxScrollDepth: maxScroll,
                scrollDepthsReached: this.state.scrollDepthTracked.length
            });
        };

        // Use beforeunload for immediate tracking
        window.addEventListener('beforeunload', trackPageExit);

        // Use visibilitychange as backup
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'hidden') {
                trackPageExit();
            }
        });

        this.log('Page exit tracking enabled');
    }

    // Public API Methods

    /**
     * Manually track an event
     */
    trackEvent(eventName, eventData = {}) {
        this.track(eventName, eventData);
    }

    /**
     * Track form submissions
     */
    trackForm(formSelector, eventName = 'form-submit') {
        document.querySelectorAll(formSelector).forEach(form => {
            form.addEventListener('submit', (e) => {
                const formData = new FormData(form);
                const data = {
                    formId: form.id || 'unnamed-form',
                    fields: formData.keys ? Array.from(formData.keys()).length : 0
                };
                this.track(eventName, data);
            });
        });
    }

    /**
     * Track file downloads
     */
    trackDownloads(selector = 'a[href*=".pdf"], a[href*=".zip"], a[href*=".doc"]') {
        document.querySelectorAll(selector).forEach(link => {
            link.addEventListener('click', () => {
                const url = new URL(link.href, window.location.href);
                const filename = url.pathname.split('/').pop();
                const extension = filename.split('.').pop();

                this.track('file-download', {
                    filename,
                    extension,
                    url: link.href
                });
            });
        });
    }

    /**
     * Track search queries (for internal search)
     */
    trackSearch(searchInputSelector, eventName = 'internal-search') {
        document.querySelectorAll(searchInputSelector).forEach(input => {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && input.value.trim()) {
                    this.track(eventName, {
                        query: input.value.trim(),
                        queryLength: input.value.trim().length
                    });
                }
            });
        });
    }

    /**
     * Get current tracking stats
     */
    getStats() {
        return {
            timeOnPage: Math.round((Date.now() - this.state.startTime) / 1000),
            scrollDepthsReached: this.state.scrollDepthTracked,
            maxScrollDepth: Math.max(...this.state.scrollDepthTracked, 0),
            isIdle: this.state.isIdle,
            elementsViewed: this.state.visibleElements.size
        };
    }

    /**
     * Cleanup and destroy tracker
     */
    destroy() {
        if (this.state.heartbeatTimer) {
            clearInterval(this.state.heartbeatTimer);
        }
        this.log('UmamiTracker destroyed');
    }
}

// Auto-initialize if data-umami-auto-track attribute is present
const autoTrackElement = document.querySelector('[data-umami-auto-track]');
if (autoTrackElement) {
    const options = {};

    // Parse configuration from data attributes
    if (autoTrackElement.dataset.umamiAutoTrackAllClicks === 'true') {
        options.autoTrackAllClicks = true;
    }
    if (autoTrackElement.dataset.umamiDebug === 'true') {
        options.debug = true;
    }
    if (autoTrackElement.dataset.umamiScrollThresholds) {
        options.scrollDepthThresholds = autoTrackElement.dataset.umamiScrollThresholds
            .split(',').map(n => parseInt(n.trim()));
    }
    if (autoTrackElement.dataset.umamiHeartbeat) {
        options.heartbeatInterval = parseInt(autoTrackElement.dataset.umamiHeartbeat);
    }

    window.umamiTracker = new UmamiTracker(options);
}

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = UmamiTracker;
}

// AMD support
if (typeof define === 'function' && define.amd) {
    define([], function () {
        return UmamiTracker;
    });
}

// Global export
window.UmamiTracker = UmamiTracker;