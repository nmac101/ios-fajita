#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
static uint32_t gpu_arena_used, gpu_arena_size = 40u << 20;
#include "alloc.inc"   /* sed -n "/BEGIN arena allocator/,/END arena allocator/p" sweet_gpu.c > alloc.inc */
#define N 400
static uint32_t off[N], sz[N]; static bool live[N];
static void check(void) {   /* live allocations never overlap and stay inside the bump region */
	for (int i = 0; i < N; i++) if (live[i]) {
		assert(off[i] + sz[i] <= gpu_arena_used && (off[i] & 0xfff) == 0);
		for (int j = i + 1; j < N; j++) if (live[j]) assert(off[i] + sz[i] <= off[j] || off[j] + sz[j] <= off[i]);
	}
}
int main(void) {
	srand(1);
	static const uint32_t sizes[] = { 16384, 163840, 229376, 458752, 4194304 };
	for (int step = 0; step < 200000; step++) {
		int i = rand() % N;
		if (live[i]) { gpu_arena_give_back(off[i], sz[i]); live[i] = false; }
		else {
			sz[i] = sizes[rand() % 5];
			uint64_t inuse = 0; for (int k = 0; k < N; k++) if (live[k]) inuse += sz[k];
			if (inuse + sz[i] > (24u << 20)) continue;   /* keep the live set where it fits */
			live[i] = gpu_arena_take(sz[i], &off[i]);
		}
		check();
	}
	for (int i = 0; i < N; i++) if (live[i]) { gpu_arena_give_back(off[i], sz[i]); live[i] = false; }
	assert(gpu_arena_used == 0);
	for (unsigned i = 0; i < GPU_ARENA_BLOCKS; i++) assert(gpu_arena_blocks[i].size == 0);
	/* after all that churn, two screen-sized buffers + one 4 MiB fit at once (the old allocator failed here) */
	uint32_t a, b, c;
	assert(gpu_arena_take(12582912, &a) && gpu_arena_take(12582912, &b) && gpu_arena_take(4194304, &c));
	/* split + reuse: free a 12 MiB block, take 16 KiB from it, the rest still serves a 4 MiB request */
	gpu_arena_give_back(a, 0); uint32_t d, e; assert(gpu_arena_take(16384, &d) && d == a);
	assert(gpu_arena_take(4194304, &e) && e == a + 16384);
	puts("arena allocator: all checks passed");
	return 0;
}
