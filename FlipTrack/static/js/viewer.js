/**
 * Image Viewer Modal functionality
 */

class ImageViewer {
    constructor() {
        this.modal = null;
        this.image = null;
        this.currentImages = [];
        this.currentIndex = 0;
        this.isOpen = false;
        
        this.init();
    }
    
    init() {
        this.createModal();
        this.bindEvents();
    }
    
    createModal() {
        // Check if modal already exists
        this.modal = document.getElementById('image-viewer');
        
        if (!this.modal) {
            // Create modal structure
            this.modal = document.createElement('div');
            this.modal.id = 'image-viewer';
            this.modal.className = 'fixed inset-0 bg-black bg-opacity-75 hidden z-50';
            this.modal.innerHTML = `
                <div class="flex items-center justify-center min-h-screen p-4">
                    <div class="relative max-w-4xl max-h-full">
                        <img id="viewer-image" src="" alt="" class="max-w-full max-h-full object-contain">
                        <button class="absolute top-4 right-4 text-white hover:text-gray-300" onclick="closeImageViewer()">
                            <svg class="h-8 w-8" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path>
                            </svg>
                        </button>
                        <div class="absolute bottom-4 left-1/2 transform -translate-x-1/2 flex space-x-2 bg-black bg-opacity-50 rounded-full px-4 py-2" id="viewer-controls" style="display: none;">
                            <button class="text-white hover:text-gray-300 p-2 rounded-full hover:bg-white hover:bg-opacity-20" onclick="imageViewer.previousImage()" title="Previous">
                                <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"></path>
                                </svg>
                            </button>
                            <button class="text-white hover:text-gray-300 p-2 rounded-full hover:bg-white hover:bg-opacity-20" onclick="imageViewer.rotateImage()" title="Rotate">
                                <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                                </svg>
                            </button>
                            <button class="text-white hover:text-gray-300 p-2 rounded-full hover:bg-white hover:bg-opacity-20" onclick="imageViewer.nextImage()" title="Next">
                                <svg class="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"></path>
                                </svg>
                            </button>
                        </div>
                        <div class="absolute top-4 left-4 text-white bg-black bg-opacity-50 px-3 py-1 rounded-full text-sm" id="image-counter" style="display: none;">
                            <span id="current-image-number">1</span> of <span id="total-images">1</span>
                        </div>
                    </div>
                </div>
            `;
            
            document.body.appendChild(this.modal);
        }
        
        this.image = document.getElementById('viewer-image');
        this.controls = document.getElementById('viewer-controls');
        this.counter = document.getElementById('image-counter');
    }
    
    bindEvents() {
        // Close on modal background click
        this.modal.addEventListener('click', (e) => {
            if (e.target === this.modal) {
                this.close();
            }
        });
        
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (!this.isOpen) return;

            // Don't hijack keyboard input when typing in form fields
            const tag = e.target.tagName;
            if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag) || e.target.isContentEditable) {
                return;
            }

            switch(e.key) {
                case 'Escape':
                    e.preventDefault();
                    this.close();
                    break;
                case 'ArrowLeft':
                    e.preventDefault();
                    this.previousImage();
                    break;
                case 'ArrowRight':
                    e.preventDefault();
                    this.nextImage();
                    break;
                case 'r':
                case 'R':
                    e.preventDefault();
                    this.rotateImage();
                    break;
                case ' ':
                    e.preventDefault();
                    this.nextImage();
                    break;
            }
        });
        
        // Prevent scrolling when modal is open
        this.modal.addEventListener('wheel', (e) => {
            e.preventDefault();
        });
        
        // Touch/swipe support for mobile
        let touchStartX = 0;
        let touchStartY = 0;
        
        this.image.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
        });
        
        this.image.addEventListener('touchend', (e) => {
            if (!touchStartX || !touchStartY) return;
            
            const touchEndX = e.changedTouches[0].clientX;
            const touchEndY = e.changedTouches[0].clientY;
            
            const deltaX = touchStartX - touchEndX;
            const deltaY = touchStartY - touchEndY;
            
            // Only process horizontal swipes that are longer than vertical swipes
            if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > 50) {
                if (deltaX > 0) {
                    this.nextImage();
                } else {
                    this.previousImage();
                }
            }
            
            touchStartX = 0;
            touchStartY = 0;
        });
    }
    
    open(src, images = null, index = 0) {
        if (!src) return;
        
        this.currentImages = images || [src];
        this.currentIndex = index;
        this.isOpen = true;
        
        this.updateImage();
        this.updateControls();
        this.updateCounter();
        
        // Show modal
        this.modal.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        
        // Focus the image for keyboard navigation
        this.image.focus();
        
        // Trigger custom event
        document.dispatchEvent(new CustomEvent('imageViewerOpen', {
            detail: { src, images: this.currentImages, index: this.currentIndex }
        }));
    }
    
    close() {
        this.isOpen = false;
        this.modal.classList.add('hidden');
        document.body.style.overflow = '';
        
        // Reset rotation
        this.image.style.transform = '';
        
        // Trigger custom event
        document.dispatchEvent(new CustomEvent('imageViewerClose'));
    }
    
    updateImage() {
        if (this.currentImages.length === 0) return;
        
        const src = this.currentImages[this.currentIndex];
        this.image.src = src;
        this.image.alt = `Image ${this.currentIndex + 1} of ${this.currentImages.length}`;
        
        // Reset any transformations
        this.image.style.transform = '';
    }
    
    updateControls() {
        if (this.currentImages.length > 1) {
            this.controls.style.display = 'flex';
        } else {
            this.controls.style.display = 'none';
        }
    }
    
    updateCounter() {
        if (this.currentImages.length > 1) {
            document.getElementById('current-image-number').textContent = this.currentIndex + 1;
            document.getElementById('total-images').textContent = this.currentImages.length;
            this.counter.style.display = 'block';
        } else {
            this.counter.style.display = 'none';
        }
    }
    
    nextImage() {
        if (this.currentImages.length <= 1) return;
        
        this.currentIndex = (this.currentIndex + 1) % this.currentImages.length;
        this.updateImage();
        this.updateCounter();
    }
    
    previousImage() {
        if (this.currentImages.length <= 1) return;
        
        this.currentIndex = this.currentIndex === 0 ? this.currentImages.length - 1 : this.currentIndex - 1;
        this.updateImage();
        this.updateCounter();
    }
    
    rotateImage() {
        const currentTransform = this.image.style.transform || '';
        const rotateMatch = currentTransform.match(/rotate\((-?\d+)deg\)/);
        const currentRotation = rotateMatch ? parseInt(rotateMatch[1]) : 0;
        const newRotation = (currentRotation + 90) % 360;
        
        this.image.style.transform = `rotate(${newRotation}deg)`;
    }
    
    // Public API methods
    goToImage(index) {
        if (index >= 0 && index < this.currentImages.length) {
            this.currentIndex = index;
            this.updateImage();
            this.updateCounter();
        }
    }
    
    setImages(images, index = 0) {
        this.currentImages = images || [];
        this.currentIndex = index;
        this.updateImage();
        this.updateControls();
        this.updateCounter();
    }
}

// Global instance
const imageViewer = new ImageViewer();

// Global functions for backward compatibility
function openImageViewer(src, images = null, index = 0) {
    imageViewer.open(src, images, index);
}

function closeImageViewer() {
    imageViewer.close();
}

// Auto-detect and setup image viewer triggers
document.addEventListener('DOMContentLoaded', function() {
    // Find all images that should open in viewer
    const viewerTriggers = document.querySelectorAll('[onclick*="openImageViewer"], .main-image, #main-image');
    
    viewerTriggers.forEach(trigger => {
        // Remove inline onclick if present and add proper event listener
        const onclickAttr = trigger.getAttribute('onclick');
        if (onclickAttr && onclickAttr.includes('openImageViewer')) {
            trigger.removeAttribute('onclick');
            
            trigger.addEventListener('click', (e) => {
                e.preventDefault();
                
                // Try to find associated gallery images
                const gallery = trigger.closest('.image-gallery');
                let images = [trigger.src];
                let index = 0;
                
                if (gallery) {
                    const galleryImages = Array.from(gallery.querySelectorAll('img[src]'));
                    images = galleryImages.map(img => img.src);
                    index = galleryImages.indexOf(trigger);
                    if (index === -1) index = 0;
                }
                
                imageViewer.open(trigger.src, images, index);
            });
            
            // Make focusable and add keyboard support
            trigger.setAttribute('tabindex', '0');
            trigger.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    trigger.click();
                }
            });
        }
    });
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { ImageViewer, imageViewer };
}
