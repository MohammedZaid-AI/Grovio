from apscheduler.schedulers.blocking import BlockingScheduler

from scheduler.scheduler import run_scheduler

scheduler = BlockingScheduler()

scheduler.add_job(
    run_scheduler,
    "interval",
    minutes=1
)

print("Scheduler started...")

scheduler.start()