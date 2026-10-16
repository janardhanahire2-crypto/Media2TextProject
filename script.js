document.addEventListener("DOMContentLoaded", function () {
    // ================= SIDEBAR =================
    const menuToggle = document.getElementById("menuToggle");
    const sidebar = document.getElementById("sidebar");
    const sidebarOverlay = document.getElementById("sidebarOverlay");
    const closeSidebar = document.getElementById("closeSidebar");

    function openSidebar() {
        if (!sidebar) return;
        sidebar.classList.add("open");
        if (sidebarOverlay) sidebarOverlay.classList.add("show");
        if (menuToggle) {
            menuToggle.classList.add("is-open");
            menuToggle.setAttribute("aria-expanded", "true");
        }
        document.body.style.overflow = "hidden";
    }

    function closeSideMenu() {
        if (!sidebar) return;
        sidebar.classList.remove("open");
        if (sidebarOverlay) sidebarOverlay.classList.remove("show");
        if (menuToggle) {
            menuToggle.classList.remove("is-open");
            menuToggle.setAttribute("aria-expanded", "false");
        }
        document.body.style.overflow = "";
    }

    if (menuToggle) menuToggle.addEventListener("click", function () {
        sidebar && sidebar.classList.contains("open") ? closeSideMenu() : openSidebar();
    });
    if (closeSidebar) closeSidebar.addEventListener("click", closeSideMenu);
    if (sidebarOverlay) sidebarOverlay.addEventListener("click", closeSideMenu);
    if (sidebar) sidebar.querySelectorAll("a").forEach(link => link.addEventListener("click", closeSideMenu));

    // ================= ADMIN DROPDOWN =================
    const adminButton = document.getElementById("adminButton");
    const userMenu = document.querySelector(".user-menu");

    if (adminButton && userMenu) {
        adminButton.addEventListener("click", function (event) {
            event.stopPropagation();
            userMenu.classList.toggle("active");
        });
    }

    document.addEventListener("click", function (event) {
        if (userMenu && !userMenu.contains(event.target)) userMenu.classList.remove("active");
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            closeSideMenu();
            if (userMenu) userMenu.classList.remove("active");
        }
    });

    // ================= SHOW PASSWORD =================
    const password = document.getElementById("loginPassword");
    const eyeButton = document.getElementById("showPassword");
    const checkbox = document.getElementById("showPasswordCheck");

    function setPasswordVisibility(show) {
        if (!password) return;
        password.type = show ? "text" : "password";
        if (eyeButton) eyeButton.innerHTML = show
            ? '<i class="fa-solid fa-eye-slash"></i>'
            : '<i class="fa-solid fa-eye"></i>';
        if (checkbox) checkbox.checked = show;
    }

    if (eyeButton) eyeButton.addEventListener("click", function () {
        setPasswordVisibility(password && password.type === "password");
    });
    if (checkbox) checkbox.addEventListener("change", function () {
        setPasswordVisibility(this.checked);
    });

    // ================= UPLOAD =================
    const fileInput = document.getElementById("media");
    const fileName = document.getElementById("fileName");
    const dropZone = document.getElementById("dropZone");
    const form = document.getElementById("uploadForm");
    const convertButton = document.getElementById("convertButton");

    if (fileInput && fileName) fileInput.addEventListener("change", function () {
        fileName.textContent = this.files.length ? this.files[0].name : "No file selected";
    });

    if (dropZone && fileInput) {
        dropZone.addEventListener("click", function () { fileInput.click(); });
        ["dragenter", "dragover"].forEach(eventName => dropZone.addEventListener(eventName, function (e) {
            e.preventDefault(); e.stopPropagation(); dropZone.classList.add("drag-active");
        }));
        ["dragleave", "drop"].forEach(eventName => dropZone.addEventListener(eventName, function (e) {
            e.preventDefault(); e.stopPropagation(); dropZone.classList.remove("drag-active");
        }));
        dropZone.addEventListener("drop", function (e) {
            if (e.dataTransfer.files.length) {
                fileInput.files = e.dataTransfer.files;
                if (fileName) fileName.textContent = e.dataTransfer.files[0].name;
            }
        });
    }

    if (form && convertButton) form.addEventListener("submit", function () {
        convertButton.disabled = true;
        convertButton.textContent = "Processing... Please wait";
    });
});
