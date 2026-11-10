document.addEventListener("alpine:init", function () {
    "use strict";

    Alpine.data("catalogue", function () {
        return {
            entries: [],
            kindFilter: "all",
            shown: 0,
            visible: {},
            // Search expansion is temporary; only clicks change the user's choices.
            opened: {},
            expanded: {},

            init() {
                // Refs are registered before init() runs, so the first render is already filtered.
                this.entries = JSON.parse(this.$refs.entries.textContent);
                const type = new URLSearchParams(window.location.search).get("type");
                if (type === "tool" || type === "data-manager") {
                    this.kindFilter = type;
                }
                this.applyFilters();
            },

            applyFilters() {
                // Read the input directly so browser-restored values survive initialisation.
                const query = (this.$refs.search.value || "").toLowerCase().trim();
                const visible = {};
                const expanded = {};
                let shown = 0;
                this.entries.forEach((entry) => {
                    const matches =
                        (query === "" || entry.search.includes(query)) &&
                        (this.kindFilter === "all" || entry.kind === this.kindFilter);
                    visible[entry.slug] = matches;
                    expanded[entry.slug] = matches && (query !== "" || this.opened[entry.slug] === true);
                    if (matches) {
                        shown += 1;
                    }
                });
                this.visible = visible;
                this.expanded = expanded;
                this.shown = shown;
            },

            selectKind(kind) {
                this.kindFilter = kind;
                const url = new URL(window.location.href);
                if (kind === "all") {
                    url.searchParams.delete("type");
                } else {
                    url.searchParams.set("type", kind);
                }
                window.history.replaceState(null, "", url);
                this.applyFilters();
            },

            isVisible(slug) {
                return this.visible[slug] !== false;
            },

            isExpanded(slug) {
                return this.expanded[slug] === true;
            },

            toggleSuite(slug) {
                const open = !this.isExpanded(slug);
                this.opened[slug] = open;
                this.expanded[slug] = open;
            },
        };
    });
});
