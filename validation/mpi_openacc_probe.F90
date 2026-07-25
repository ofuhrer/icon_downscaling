program mpi_openacc_probe
    use mpi
    use openacc, only : acc_device_kind, acc_device_nvidia, acc_get_num_devices, &
                         acc_set_device_num, acc_init, acc_shutdown
    implicit none

    integer :: ierr, rank, local_rank, local_comm, ndev
    integer(acc_device_kind) :: devtype

    write(*,'(A)') 'probe: before MPI_Init'
    flush(6)
    call MPI_Init(ierr)
    call MPI_Comm_rank(MPI_COMM_WORLD, rank, ierr)
    call MPI_Comm_split_type(MPI_COMM_WORLD, MPI_COMM_TYPE_SHARED, 0, MPI_INFO_NULL, local_comm, ierr)
    call MPI_Comm_rank(local_comm, local_rank, ierr)
    write(*,'(A,I0,A,I0)') 'probe: after MPI_Init rank=', rank, ' local=', local_rank
    flush(6)

    if (local_rank < 4) then
        devtype = acc_device_nvidia
        ndev = acc_get_num_devices(devtype)
        write(*,'(A,I0,A,I0)') 'probe: compute rank=', rank, ' visible_gpus=', ndev
        flush(6)
        if (ndev /= 1) call MPI_Abort(MPI_COMM_WORLD, 2, ierr)
        call acc_set_device_num(0, devtype)
        call acc_init(devtype)
        write(*,'(A,I0)') 'probe: OpenACC initialized rank=', rank
        flush(6)
    else
        write(*,'(A,I0)') 'probe: I/O rank skipped OpenACC rank=', rank
        flush(6)
    endif

    call MPI_Barrier(MPI_COMM_WORLD, ierr)
    if (local_rank < 4) call acc_shutdown(acc_device_nvidia)
    call MPI_Finalize(ierr)
end program mpi_openacc_probe
