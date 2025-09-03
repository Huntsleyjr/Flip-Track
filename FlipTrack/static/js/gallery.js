/**
 * Image Gallery functionality for the inventory tracking application
 */

class ImageGallery {
    constructor(container) {
        this.container = container;
        this.mainImage = null;
        this.thumbnails = [];
        this.currentIndex = 0;
        
        this.init();
    }
    
    init() {
        this.mainImage = this.container.querySelector('#main-image');
        this.thumbnails = Array.from(this.container.querySelectorAll('.thumbnail, [onclick*="setMainImage"]'));
        
        if (this.thumbnails.length > 0) {
            this.bindEvents();
            this.setActiveThumbnail(0);
        }
    }
    
    bindEvents() {
        // Add click handlers to thumbnails
        this.thumbnails.forEach((thumb, index) => {
            thumb.addEventListener('click', (e) => {
                e.preventDefault();
                this.setMainImage(thumb.src || thumb.dataset.src, index);
            });
            
            // Add keyboard navigation
            thumb.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.setMainImage(thumb.src || thumb.dataset.src, index);
                }
            });
        });
        
        // Keyboard navigation for main image
        if (this.mainImage) {
            this.mainImage.addEventListener('keydown', (e) => {
                switch(e.key) {
                    case 'ArrowLeft':
                        e.preventDefault();
                        this.previousImage();
                        break;
                    case 'ArrowRight':
                        e.preventDefault();
                        this.nextImage();
                        break;
                    case 'Escape':
                        e.preventDefault();
                        this.closeGallery();
                        break;
                }
            });
            
            // Make main image focusable
            this.mainImage.setAttribute('tabindex', '0');
        }
    }
    
    setMainImage(src, index) {
        if (!this.mainImage || !src) return;
        
        // Update main image
        this.mainImage.src = src;
        this.currentIndex = index !== undefined ? index : this.currentIndex;
        
        // Update thumbnails
        this.setActiveThumbnail(this.currentIndex);
        
        // Update alt text
        this.mainImage.alt = `Image ${this.currentIndex + 1} of ${this.thumbnails.length}`;
        
        // Trigger custom event
        this.container.dispatchEvent(new CustomEvent('imageChanged', {
            detail: { src, index: this.currentIndex }
        }));
    }
    
    setActiveThumbnail(index) {
        this.thumbnails.forEach((thumb, i) => {
            thumb.classList.remove('active', 'border-indigo-500');
            if (i === index) {
                thumb.classList.add('active', 'border-indigo-500');
            } else {
                thumb.classList.add('border-transparent');
            }
        });
    }
    
    nextImage() {
        if (this.thumbnails.length <= 1) return;
        
        const nextIndex = (this.currentIndex + 1) % this.thumbnails.length;
        const nextThumb = this.thumbnails[nextIndex];
        this.setMainImage(nextThumb.src || nextThumb.dataset.src, nextIndex);
    }
    
    previousImage() {
        if (this.thumbnails.length <= 1) return;
        
        const prevIndex = this.currentIndex === 0 ? this.thumbnails.length - 1 : this.currentIndex - 1;
        const prevThumb = this.thumbnails[prevIndex];
        this.setMainImage(prevThumb.src || prevThumb.dataset.src, prevIndex);
    }
    
    closeGallery() {
        // This can be overridden by specific implementations
        this.container.dispatchEvent(new CustomEvent('galleryClose'));
    }
    
    // Public API methods
    goToImage(index) {
        if (index >= 0 && index < this.thumbnails.length) {
            const thumb = this.thumbnails[index];
            this.setMainImage(thumb.src || thumb.dataset.src, index);
        }
    }
    
    getCurrentIndex() {
        return this.currentIndex;
    }
    
    getTotalImages() {
        return this.thumbnails.length;
    }
}

// Global functions for backward compatibility with existing templates
function setMainImage(src, thumbElement) {
    const gallery = thumbElement.closest('.image-gallery');
    if (gallery && gallery._galleryInstance) {
        const thumbnails = Array.from(gallery.querySelectorAll('.thumbnail, [onclick*="setMainImage"]'));
        const index = thumbnails.indexOf(thumbElement);
        gallery._galleryInstance.setMainImage(src, index);
    } else {
        // Fallback for non-gallery usage
        const mainImage = document.getElementById('main-image');
        if (mainImage) {
            mainImage.src = src;
        }
        
        // Update thumbnail borders
        const thumbnails = document.querySelectorAll('img[onclick*="setMainImage"]');
        thumbnails.forEach(thumb => {
            thumb.classList.remove('border-indigo-500');
            thumb.classList.add('border-transparent');
        });
        
        if (thumbElement) {
            thumbElement.classList.remove('border-transparent');
            thumbElement.classList.add('border-indigo-500');
        }
    }
}

// Initialize galleries when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    const galleries = document.querySelectorAll('.image-gallery');
    galleries.forEach(gallery => {
        gallery._galleryInstance = new ImageGallery(gallery);
    });
});

// Export for module usage
if (typeof module !== 'undefined' && module.exports) {
    module.exports = ImageGallery;
}
