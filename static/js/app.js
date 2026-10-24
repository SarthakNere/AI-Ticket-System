document.addEventListener("DOMContentLoaded", function () {
  const toggle = document.getElementById("menuToggle");
  const sidebar = document.getElementById("sidebar");
  if (toggle && sidebar) {
    toggle.addEventListener("click", () => sidebar.classList.toggle("sidebar-open"));
  }

  const landingToggle = document.querySelector(".landing-menu-toggle");
  const landingNav = document.getElementById("landingNav");
  if (landingToggle && landingNav) {
    landingToggle.addEventListener("click", () => {
      const isOpen = landingNav.classList.toggle("landing-nav-open");
      landingToggle.setAttribute("aria-expanded", String(isOpen));
    });
  }

  const revealItems = document.querySelectorAll(".feature-card, .landing-cta");
  if (revealItems.length) {
    const showItems = () => revealItems.forEach((item) => item.classList.add("is-visible"));
    if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const observer = new IntersectionObserver((entries, activeObserver) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            activeObserver.unobserve(entry.target);
          }
        });
      }, { threshold: 0.12 });
      revealItems.forEach((item, index) => {
        item.classList.add("reveal-on-scroll");
        item.style.setProperty("--reveal-delay", `${(index % 3) * 75}ms`);
        observer.observe(item);
      });
    } else {
      showItems();
    }
  }

  const interactiveCards = document.querySelectorAll(".interactive-card");
  const supportsFinePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (supportsFinePointer && !prefersReducedMotion) {
    interactiveCards.forEach((card) => {
      card.addEventListener("pointermove", (event) => {
        const bounds = card.getBoundingClientRect();
        const x = (event.clientX - bounds.left) / bounds.width;
        const y = (event.clientY - bounds.top) / bounds.height;
        card.style.setProperty("--pointer-x", `${x * 100}%`);
        card.style.setProperty("--pointer-y", `${y * 100}%`);
        card.style.setProperty("--tilt-x", `${(x - 0.5) * 1.5}deg`);
        card.style.setProperty("--tilt-y", `${(0.5 - y) * 1.5}deg`);
      });
      card.addEventListener("pointerleave", () => {
        card.style.removeProperty("--pointer-x");
        card.style.removeProperty("--pointer-y");
        card.style.removeProperty("--tilt-x");
        card.style.removeProperty("--tilt-y");
      });
    });
  }

  const copyButton = document.getElementById("copyReply");
  const reply = document.getElementById("replyText");
  if (copyButton && reply) {
    copyButton.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(reply.value);
        copyButton.innerHTML = '<i class="bi bi-check2 me-1"></i>Copied';
        setTimeout(() => { copyButton.innerHTML = '<i class="bi bi-clipboard me-1"></i>Copy'; }, 1800);
      } catch (error) {
        reply.select();
        document.execCommand("copy");
        copyButton.innerHTML = '<i class="bi bi-check2 me-1"></i>Copied';
      }
    });
  }
});
