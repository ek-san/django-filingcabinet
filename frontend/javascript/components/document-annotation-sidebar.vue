<template>
  <div class="annotations">
    <RecycleScroller
      class="scroller"
      :items="pages"
      page-mode
      key-field="number"
      sizeField="normalSize"
      v-slot="{ item }"
    >
      <page-annotations
        :annotations="annotations[item.number] || []"
        :page="item"
        :current-annotation="currentAnnotation"
        :can-annotate="canAnnotate"
        :active-annotation-form="activeAnnotationForm"
        @currentannotation="$emit('currentannotation', $event)"
        @activateannotationform="$emit('activateannotationform', $event)"
        @deleteannotation="$emit('deleteannotation', $event)"
      />
    </RecycleScroller>
  </div>
</template>

<script>

import 'intersection-observer'

import { RecycleScroller } from 'vue-virtual-scroller'

import PageAnnotations from './document-annotations.vue'

export default {
  name: 'document-annotation-sidebar',
  props: [
    'document', 'annotations', 'currentAnnotation', 'canAnnotate',
    'activeAnnotationForm'
  ],
  components: {
    RecycleScroller,
    PageAnnotations
  },
  computed: {
    pages () {
      return this.document.pages
    }
  },
}
</script>

<style lang="scss">
@import '~vue-virtual-scroller/dist/vue-virtual-scroller.css';
.annotations {
  padding: 1rem 0;
}
</style>
