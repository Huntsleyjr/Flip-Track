/**
 * Theme management for the inventory tracking application
 */

class ThemeManager {
    constructor() {
        this.currentTheme = 'system';
        this.systemTheme = 'light';
        this.storageKey = 'inventory-theme-preference';
        
        this.init();
    }
    
    init() {
        // Get saved theme preference or default to system
        this.currentTheme = localStorage.getItem(this.storageKey) || 'system';
        
        // Detect system theme
        this.detectSystemTheme();
        
        // Apply initial theme
        this.applyTheme();
        
        // Set up system theme change listener
        this.setupSystemThemeListener();
        
        // Set up theme toggle button
        this.setupThemeToggle();
        
        // Set up time-based theme switching
        this.setupTimeBasedTheme();
    }
    
    detectSystemTheme() {
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            this.systemTheme = 'dark';
        } else {
            this.systemTheme = 'light';
        }
    }
    
    setupSystemThemeListener() {
        if (window.matchMedia) {
            const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
            mediaQuery.addEventListener('change', (e) => {
                this.systemTheme = e.matches ? 'dark' : 'light';
                
                if (this.currentTheme === 'system') {
                    this.applyTheme();
                }
            });
        }
    }
    
    setupThemeToggle() {
        const themeToggle = document.getElementById('theme-toggle');
        if (themeToggle) {
            themeToggle.addEventListener('click', () => {
                this.toggleTheme();
            });
        }
        
        // Also handle theme select dropdown if present
        const themeSelect = document.getElementById('theme');
        if (themeSelect) {
            themeSelect.value = this.currentTheme;
            themeSelect.addEventListener('change', (e) => {
                this.setTheme(e.target.value);
            });
        }
    }
    
    setupTimeBasedTheme() {
        // Check if time-based theming is enabled (could be a setting)
        const timeBasedEnabled = localStorage.getItem('time-based-theme') === 'true';
        
        if (timeBasedEnabled) {
            setInterval(() => {
                this.checkTimeBasedTheme();
            }, 60000); // Check every minute
            
            this.checkTimeBasedTheme(); // Initial check
        }
    }
    
    checkTimeBasedTheme() {
        if (this.currentTheme !== 'system') return;
        
        const now = new Date();
        const hour = now.getHours();
        
        // Dark theme from 6 PM to 6 AM
        const shouldBeDark = hour >= 18 || hour < 6;
        const newSystemTheme = shouldBeDark ? 'dark' : 'light';
        
        if (newSystemTheme !== this.systemTheme) {
            this.systemTheme = newSystemTheme;
            this.applyTheme();
        }
    }
    
    
    applyTheme() {
        const effectiveTheme = this.getEffectiveTheme();
        const html = document.documentElement;
        
        // Remove existing theme classes
        html.classList.remove('light', 'dark');
        
        // Add current theme class
        html.classList.add(effectiveTheme);
        
        // Update meta theme-color for mobile browsers
        this.updateMetaThemeColor(effectiveTheme);
        
        // Update theme toggle icon
        this.updateThemeToggleIcon(effectiveTheme);
        
        // Store preference (but not if it's system-derived)
        if (this.currentTheme !== 'system' || localStorage.getItem(this.storageKey)) {
            localStorage.setItem(this.storageKey, this.currentTheme);
        }
        
        // Trigger custom event
        document.dispatchEvent(new CustomEvent('themeChanged', {
            detail: { 
                theme: this.currentTheme, 
                effectiveTheme: effectiveTheme 
            }
        }));
    }
    
    updateMetaThemeColor(theme) {
        let themeColor = theme === 'dark' ? '#1f2937' : '#ffffff';
        
        let metaThemeColor = document.querySelector('meta[name="theme-color"]');
        if (!metaThemeColor) {
            metaThemeColor = document.createElement('meta');
            metaThemeColor.name = 'theme-color';
            document.getElementsByTagName('head')[0].appendChild(metaThemeColor);
        }
        metaThemeColor.content = themeColor;
    }
    
    updateThemeToggleIcon(theme) {
        const sunIcon = document.getElementById('theme-toggle-sun');
        const moonIcon = document.getElementById('theme-toggle-moon');

        if (sunIcon && moonIcon) {
            if (theme === 'dark') {
                sunIcon.classList.remove('hidden');
                moonIcon.classList.add('hidden');
            } else {
                sunIcon.classList.add('hidden');
                moonIcon.classList.remove('hidden');
            }
        }
    }
    
    setTheme(theme) {
        if (['light', 'dark', 'system'].includes(theme)) {
            this.currentTheme = theme;
            this.applyTheme();
        }
    }
    
    toggleTheme() {
        const effectiveTheme = this.getEffectiveTheme();
        const newTheme = effectiveTheme === 'dark' ? 'light' : 'dark';
        this.setTheme(newTheme);
    }
    
    // Public API methods
    getCurrentTheme() {
        return this.currentTheme;
    }
    
    getEffectiveTheme() {
        return this.currentTheme === 'system' ? this.systemTheme : this.currentTheme;
    }
    
    isSystemTheme() {
        return this.currentTheme === 'system';
    }
    
    enableTimeBasedTheme(enabled = true) {
        localStorage.setItem('time-based-theme', enabled.toString());
        if (enabled) {
            this.setupTimeBasedTheme();
        }
    }
}

// Global theme manager instance
let themeManager;

// Initialize immediately
document.addEventListener('DOMContentLoaded', function() {
    themeManager = new ThemeManager();
    window.themeManager = themeManager;
    initializeThemeAfterLoad();
});

// Global functions for backward compatibility
function setTheme(theme) {
    if (themeManager) {
        themeManager.setTheme(theme);
    }
}

function toggleTheme() {
    if (themeManager) {
        themeManager.toggleTheme();
    }
}

function getCurrentTheme() {
    return themeManager ? themeManager.getCurrentTheme() : 'system';
}

// Additional initialization after theme manager is created
function initializeThemeAfterLoad() {
    if (themeManager) {
        // Ensure theme is applied early to prevent flash
        themeManager.applyTheme();
        
        // Add transition classes after a brief delay to prevent flash during initial load
        setTimeout(() => {
            document.body.classList.add('transition-colors', 'duration-200');
        }, 100);
    }
}

// Handle page visibility changes to update time-based theme
document.addEventListener('visibilitychange', function() {
    if (!document.hidden && themeManager && themeManager.currentTheme === 'system') {
        themeManager.checkTimeBasedTheme();
    }
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ThemeManager, themeManager };
}

// Additional utility functions for theme-aware components
window.themeUtils = {
    // Get appropriate color based on current theme
    getThemeColor: function(lightColor, darkColor) {
        const theme = themeManager ? themeManager.getEffectiveTheme() : 'light';
        return theme === 'dark' ? darkColor : lightColor;
    },
    
    // Check if current theme is dark
    isDark: function() {
        return themeManager ? themeManager.getEffectiveTheme() === 'dark' : false;
    },
    
    // Listen for theme changes
    onThemeChange: function(callback) {
        document.addEventListener('themeChanged', callback);
    },
    
    // Remove theme change listener
    offThemeChange: function(callback) {
        document.removeEventListener('themeChanged', callback);
    }
};
