/**
 * Theme Management System for Text2SQL Application
 * Provides centralized theme switching with a persisted user preference
 * (localStorage); sessionStorage is only a per-tab fallback.
 */

class ThemeManager {
    constructor() {
        this.themes = {
            dark: {
                name: 'Dark Theme',
                icon: 'fas fa-moon',
            },
            light: {
                name: 'Light Theme',
                icon: 'fas fa-sun',
            },
            lightColored: {
                name: 'Light Colored',
                icon: 'fas fa-palette',
            }
        };
        
    // Default to light theme when no preference is stored
    this.currentTheme = this.getStoredTheme() || 'light';
        this.init();
    }
    
    init() {
        // Apply the stored theme on page load. Boot never writes storage: the
        // preference is persisted only when the user picks a theme.
        this.applyTheme(this.currentTheme, { persist: false });
        
        // Listen for theme change events immediately
        this.attachEventListeners();
        
        // Also attach after DOM is ready for any dynamically loaded content
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => {
                this.updateThemeSelector();
            });
        } else {
            this.updateThemeSelector();
        }
    }
    
    getStoredTheme() {
        try {
            // The persisted, cross-session choice wins; the per-tab session value
            // is only a fallback for when nothing has been persisted yet.
            return localStorage.getItem('selectedTheme') || sessionStorage.getItem('selectedTheme');
        } catch (error) {
            console.warn('Could not access storage for theme:', error);
            return null;
        }
    }
    
    storeTheme(themeName) {
        try {
            // Store in session storage for current session
            sessionStorage.setItem('selectedTheme', themeName);
            // Also store in local storage as fallback
            localStorage.setItem('selectedTheme', themeName);
        } catch (error) {
            console.warn('Could not store theme preference:', error);
        }
    }
    
    applyTheme(themeName, options) {
        const persist = !options || options.persist !== false;
        const theme = this.themes[themeName];
        if (!theme) {
            console.warn(`Theme '${themeName}' not found, falling back to light theme`);
            return this.applyTheme('light', { persist: persist });
        }

        // The palette lives in static/css/themes.css (single source of truth).
        // Writing the same tokens inline here used to override every CSS change,
        // so this method only manages the theme class, persistence and events.
        
        // Update theme class on both html and body without nuking other classes
        const updateThemeClass = (el) => {
            if (!el) return;
            const classes = Array.from(el.classList);
            classes.filter(c => c.startsWith('theme-')).forEach(c => el.classList.remove(c));
            el.classList.add(`theme-${themeName}`);
        };
        updateThemeClass(document.body);
        updateThemeClass(document.documentElement);
        
        // Update highlight.js theme based on current theme
        this.updateHighlightTheme(themeName);
        
        // Store the current theme
        this.currentTheme = themeName;
        if (persist) this.storeTheme(themeName);
        
        // Update the theme selector UI
        this.updateThemeSelector();        // Dispatch custom event for components that need to react to theme changes
        document.dispatchEvent(new CustomEvent('themeChanged', {
            detail: { theme: themeName }
        }));
        
        console.log(`Applied theme: ${theme.name}`);
    }
    
    updateHighlightTheme(themeName) {
        const highlightLink = document.getElementById('highlight-theme');
        if (highlightLink) {
            // Map our themes to appropriate highlight.js themes
            const highlightThemes = {
                dark: 'github-dark',
                light: 'github',
                lightColored: 'github'
            };
            
            const highlightTheme = highlightThemes[themeName] || 'github-dark';
            const newHref = `/static/vendor/highlight/styles/${highlightTheme}.min.css`;
            
            // Only update if the theme is different
            if (highlightLink.href !== newHref) {
                highlightLink.href = newHref;
                console.log(`Applied highlight.js theme: ${highlightTheme}`);
            }
        }
    }
    
    switchTheme(themeName) {
        if (this.themes[themeName]) {
            this.applyTheme(themeName);
        }
    }
    
    getAvailableThemes() {
        return Object.keys(this.themes).map(key => ({
            key,
            name: this.themes[key].name,
            icon: this.themes[key].icon,
            current: key === this.currentTheme
        }));
    }
    
    attachEventListeners() {
        // Listen for theme selector clicks
        document.addEventListener('click', (e) => {
            const themeElement = e.target.closest('[data-theme]');
            if (themeElement) {
                const themeName = themeElement.getAttribute('data-theme');
                if (this.themes[themeName]) {
                    this.switchTheme(themeName);
                    e.preventDefault();
                }
            }
        });
    }
    
    updateThemeSelector() {
        // Update any theme selector UI elements
        const themeItems = document.querySelectorAll('[data-theme]');
        themeItems.forEach(item => {
            const themeName = item.getAttribute('data-theme');
            const isActive = themeName === this.currentTheme;
            
            // Update active state
            item.classList.toggle('active', isActive);
            
            // Update check mark or active indicator
            const checkIcon = item.querySelector('.theme-check');
            if (checkIcon) {
                checkIcon.style.display = isActive ? 'inline' : 'none';
            }
        });
        
        // Update current theme indicator
        const currentThemeIndicator = document.querySelector('#currentThemeIndicator');
        if (currentThemeIndicator) {
            const theme = this.themes[this.currentTheme];
            currentThemeIndicator.innerHTML = `<i class="${theme.icon} me-2"></i>${theme.name}`;
        }
    }
    
    getCurrentTheme() {
        return this.currentTheme;
    }
}

// Initialize theme manager
const themeManager = new ThemeManager();

// Export for global access
window.themeManager = themeManager;
