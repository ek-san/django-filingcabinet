<template>
  <div class="document-search-pages bg-secondary text-light" :style="{'height': height}">
    <RecycleScroller
      class="scroller"
      :items="pages"
      key-field="number"
      :item-size="116"
      v-slot="{ item }"
    >
      <document-search-preview
        :key="item.number"
        :matches="item"
        :page="documentPages[item.number - 1]"
        :current-page="currentPage"
        @navigate="navigate(item)"
      />
    </RecycleScroller>
  </div>
</template>

<script>

import 'intersection-observer'

import { RecycleScroller } from 'vue-virtual-scroller'

import DocumentSearchPreview from './document-search-preview.vue'

export default {
  name: 'document-search-sidebar',
  props: ['documentPages', 'pages', 'currentPage', 'height'],
  components: {
    DocumentSearchPreview,
    RecycleScroller
  },
  computed: {
  },
  methods: {
    navigate (item) {
      let searchIndex = this.pages.findIndex((i) => i === item)
      this.$emit('navigate', {
        number: item.number,
        source: 'sidebar',
        searchIndex: searchIndex
      })
    }
  }
}
</script>

<style lang="scss">
.document-search-pages {
  padding: 0.5rem 0;
}
</style>
