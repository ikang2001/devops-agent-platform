# Change regression investigation

Change 只能作为候选线索。必须同时存在当前窗口的错误率、日志或 Trace 变化，且
服务、版本与时间顺序一致，才允许提高候选排序。只有 Change 没有直接运行时证据时，
应返回 `UNDETERMINED` 或保守候选，避免把相关性写成因果结论。
